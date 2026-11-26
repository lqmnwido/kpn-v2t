package com.example.asr.worker.activities;

import com.example.asr.worker.config.PythonSidecarManager;
import com.example.asr.worker.model.ChunkInfo;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.minio.BucketExistsArgs;
import io.minio.GetObjectArgs;
import io.minio.MakeBucketArgs;
import io.minio.MinioClient;
import io.temporal.spring.boot.ActivityImpl;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.HashMap;
import java.util.Map;

@Component("diarizationActivitiesImpl")
@ActivityImpl(workers = {"asrWorker"})
public class DiarizationActivitiesImpl implements DiarizationActivities {

    private static final Logger logger = LoggerFactory.getLogger(DiarizationActivitiesImpl.class);

    private final MinioClient minioClient;
    private final String bucket;
    private final String minioUrl;
    private final String minioAccessKey;
    private final String minioSecretKey;
    private final PythonSidecarManager sidecarManager;
    private final Path workingDir;

    public DiarizationActivitiesImpl(MinioClient minioClient,
                                     @Value("${app.minio.bucket}") String bucket,
                                     @Value("${app.minio.url}") String minioUrl,
                                     @Value("${app.minio.access-key}") String minioAccessKey,
                                     @Value("${app.minio.secret-key}") String minioSecretKey,
                                     PythonSidecarManager sidecarManager,
                                     @Value("${app.tools.working-dir}") String workingDir) {
        this.minioClient = minioClient;
        this.bucket = bucket;
        this.minioUrl = minioUrl;
        this.minioAccessKey = minioAccessKey;
        this.minioSecretKey = minioSecretKey;
        this.sidecarManager = sidecarManager;
        this.workingDir = Path.of(workingDir).toAbsolutePath().normalize();
    }

    @PostConstruct
    public void ensureBucket() throws Exception {
        Files.createDirectories(workingDir);
        boolean exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
        if (!exists) {
            minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
        }
    }

    @Override
    public String prepareRecording(String normalizedKey, String jobId, String bucket) {
        try {
            Path jobDir = workingDir.resolve(jobId);
            Files.createDirectories(jobDir);
            Path recording = jobDir.resolve("diarization-recording.wav");
            downloadObject(normalizedKey, recording, bucket);
            Map<String, Object> task = new HashMap<>();
            task.put("action", "prepare_recording");
            task.put("audio_path", recording.toAbsolutePath().toString());
            task.put("job_id", jobId);
            task.put("minio_url", minioUrl);
            task.put("minio_access_key", minioAccessKey);
            task.put("minio_secret_key", minioSecretKey);
            task.put("minio_bucket", bucket);
            JsonNode response = sidecarManager.sendTask(task);
            if (response == null || !"success".equals(response.path("status").asText())
                    || !response.hasNonNull("key")) {
                throw new IllegalStateException("Recording diarization failed: "
                        + (response == null ? "no response" : response.path("message").asText()));
            }
            return response.get("key").asText();
        } catch (Exception e) {
            throw new RuntimeException("Failed to prepare recording " + jobId, e);
        }
    }

    @Override
    public String diarizeChunk(ChunkInfo chunkInfo, String segmentsJson, String jobId, String bucket) {
        try {
            Path jobDir = workingDir.resolve(jobId);
            Files.createDirectories(jobDir);

            Path chunkFile = jobDir.resolve("chunk-%03d.wav".formatted(chunkInfo.index()));
            if (!Files.exists(chunkFile)) {
                downloadObject(chunkInfo.chunkKey(), chunkFile, bucket);
            }

            logger.info("Diarizing chunk {} via Persistent Pipe-Worker", chunkInfo.index());

            Map<String, Object> task = new HashMap<>();
            task.put("action", "diarize");
            task.put("audio_path", chunkFile.toAbsolutePath().toString());
            task.put("segments_json", segmentsJson);
            task.put("job_id", jobId);
            task.put("chunk_index", chunkInfo.index());
            task.put("start_offset", chunkInfo.startOffset());
            task.put("minio_url", minioUrl);
            task.put("minio_access_key", minioAccessKey);
            task.put("minio_secret_key", minioSecretKey);
            task.put("minio_bucket", bucket);

            JsonNode response = sidecarManager.sendTask(task);
            
            if (response == null || response.get("status") == null) {
                throw new RuntimeException("Diarization failed: Sidecar returned null or missing status. Raw response: " + (response != null ? response.toString() : "null"));
            }

            if (!"success".equals(response.get("status").asText())) {
                String errorMsg = (response.get("message") != null) ? response.get("message").asText() : "Unknown error";
                throw new RuntimeException("Diarization failed: " + errorMsg);
            }

            JsonNode keyNode = response.get("key");
            if (keyNode == null) {
                throw new RuntimeException("Diarization failed: 'key' field is missing in successful response. Raw response: " + response.toString());
            }

            return keyNode.asText();
        } catch (Exception e) {
            throw new RuntimeException("Failed to diarize chunk " + chunkInfo.index(), e);
        }
    }

    private void downloadObject(String objectKey, Path targetPath, String bucket) throws Exception {
        try (InputStream inputStream = minioClient.getObject(
                GetObjectArgs.builder().bucket(bucket).object(objectKey).build())) {
            Files.copy(inputStream, targetPath, StandardCopyOption.REPLACE_EXISTING);
        }
    }
}
