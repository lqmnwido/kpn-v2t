package com.example.asr.worker.activities;

import com.example.asr.worker.config.WorkerProcesses;

import com.example.asr.worker.model.ChunkInfo;
import io.minio.BucketExistsArgs;
import io.minio.GetObjectArgs;
import io.minio.MakeBucketArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.temporal.spring.boot.ActivityImpl;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.stream.Collectors;

@Component("mediaActivitiesImpl")
@ActivityImpl(workers = {"mediaWorker"})
public class MediaActivitiesImpl implements MediaActivities {

    private final MinioClient minioClient;
    private final String bucket;
    private final String ffmpegPath;
    private final String audioFilter;
    private final String pythonExecutable;
    private final String enhanceScript;
    private final String ffprobePath;
    private final Path workingDir;
    private final WorkerProcesses workerProcesses;
    private final int chunkSeconds;

    public MediaActivitiesImpl(MinioClient minioClient,
                               @Value("${app.minio.bucket}") String bucket,
                               @Value("${app.tools.ffmpeg-path}") String ffmpegPath,
                               @Value("${app.tools.ffprobe-path}") String ffprobePath,
                               @Value("${app.tools.working-dir}") String workingDir,
                               @Value("${app.tools.chunk-seconds:30}") int chunkSeconds,
                               @Value("${ASR_AUDIO_FILTER:auto}") String audioFilter,
                               @Value("${app.tools.python-executable:python/venv/bin/python}") String pythonExecutable,
                               WorkerProcesses workerProcesses) {
        this.minioClient = minioClient;
        this.bucket = bucket;
        this.ffmpegPath = resolveCommand(ffmpegPath);
        this.ffprobePath = resolveCommand(ffprobePath);
        this.workerProcesses = workerProcesses;
        this.workingDir = Path.of(workingDir).toAbsolutePath().normalize();
        this.chunkSeconds = chunkSeconds;
        this.audioFilter = audioFilter;
        this.pythonExecutable = Path.of(pythonExecutable).toAbsolutePath().toString();
        this.enhanceScript = Path.of("python/enhance_audio.py").toAbsolutePath().toString();
        if (chunkSeconds <= 2) {
            throw new IllegalArgumentException("Audio chunks must exceed the two-second overlap");
        }
    }

    private static String resolveCommand(String executable) {
        Path path = Path.of(executable);
        if (path.getParent() == null && !Files.isRegularFile(path)) {
            return executable;
        }
        return path.toAbsolutePath().toString();
    }

    @PostConstruct
    public void init() throws Exception {
        Files.createDirectories(workingDir);
        boolean exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
        if (!exists) {
            minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
        }
    }

    @Override
    public String validateAndNormalize(String inputObjectKey, String jobId, String bucket) {
        try {
            Path jobDir = workingDir.resolve(jobId);
            Files.createDirectories(jobDir);

            String inputName = Path.of(inputObjectKey).getFileName().toString();
            Path inputFile = jobDir.resolve(inputName);
            downloadObject(inputObjectKey, inputFile, bucket);

            Path normalizedFile = jobDir.resolve("audio-normalized.wav");
            runCommand(List.of(
                    pythonExecutable, enhanceScript,
                    "--input", inputFile.toAbsolutePath().toString(),
                    "--output-dir", jobDir.toAbsolutePath().toString(),
                    "--ffmpeg", ffmpegPath,
                    "--ffprobe", ffprobePath,
                    "--audio-filter", audioFilter
            ), jobDir);

            String normalizedKey = "normalized/%s/audio.wav".formatted(jobId);
            uploadObject(normalizedKey, normalizedFile, "audio/wav", bucket);
            uploadObject("normalized/%s/audio-enhanced.m4a".formatted(jobId),
                    jobDir.resolve("audio-enhanced.m4a"), "audio/mp4", bucket);
            uploadObject("normalized/%s/audio-enhancement.json".formatted(jobId),
                    jobDir.resolve("audio-enhancement.json"), "application/json", bucket);
            return normalizedKey;
        } catch (Exception e) {
            throw new RuntimeException("Failed to validate and normalize media", e);
        }
    }

    @Override
    public List<ChunkInfo> chunkAudio(String normalizedObjectKey, String jobId, String bucket) {
        try {
            Path jobDir = workingDir.resolve(jobId);
            Files.createDirectories(jobDir);

            Path normalizedFile = jobDir.resolve("audio-normalized.wav");
            downloadObject(normalizedObjectKey, normalizedFile, bucket);

            Path chunkDir = jobDir.resolve("chunks");
            Files.createDirectories(chunkDir);

            // Get total duration of the audio
            String durationStr = runCommandAndGetOutput(List.of(
                    ffprobePath, "-v", "error", "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1", normalizedFile.toString()
            ), jobDir).trim();
            double totalDuration = Double.parseDouble(durationStr);

            List<ChunkInfo> chunks = new ArrayList<>();
            int index = 1;
            int overlap = 2; // 2 seconds overlap
            int effectiveStep = chunkSeconds - overlap;

            for (double start = 0; start < totalDuration; start += effectiveStep) {
                double duration = Math.min(chunkSeconds, totalDuration - start);
                Path chunkFile = chunkDir.resolve("chunk-%03d.wav".formatted(index));
                
                runCommand(List.of(
                        ffmpegPath, "-y",
                        "-ss", String.valueOf(start),
                        "-t", String.valueOf(duration),
                        "-i", normalizedFile.toString(),
                        //"-c", "copy",
                        chunkFile.toString()
                ), jobDir);

                String chunkKey = "chunks/%s/%s".formatted(jobId, chunkFile.getFileName().toString());
                uploadObject(chunkKey, chunkFile, "audio/wav", bucket);
                
                chunks.add(new ChunkInfo(chunkKey, index, start)); 
                index++;
            }

            if (chunks.isEmpty()) {
                throw new IllegalStateException("No chunks were produced");
            }

            return chunks;
        } catch (Exception e) {
            throw new RuntimeException("Failed to chunk audio with overlap", e);
        }
    }

    private String runCommandAndGetOutput(List<String> command, Path workDir) throws Exception {
        ProcessBuilder processBuilder = workerProcesses.create(command);
        processBuilder.directory(workDir.toFile());
        processBuilder.redirectErrorStream(true);
        Process process = processBuilder.start();
        String output;
        try (InputStream inputStream = process.getInputStream()) {
            output = new String(inputStream.readAllBytes());
        }
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IllegalStateException("Command failed (exit=" + exitCode + "): " + String.join(" ", command) + "\n" + output);
        }
        return output;
    }

    private void downloadObject(String objectKey, Path targetPath, String bucket) throws Exception {
        try (InputStream inputStream = minioClient.getObject(
                GetObjectArgs.builder().bucket(bucket).object(objectKey).build())) {
            Files.copy(inputStream, targetPath, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private void uploadObject(String objectKey, Path filePath, String contentType, String bucket) throws Exception {
        try (InputStream inputStream = Files.newInputStream(filePath)) {
            minioClient.putObject(
                    PutObjectArgs.builder()
                            .bucket(bucket)
                            .object(objectKey)
                            .stream(inputStream, Files.size(filePath), -1)
                            .contentType(contentType)
                            .build());
        }
    }

    private void runCommand(List<String> command, Path workDir) throws Exception {
        ProcessBuilder processBuilder = workerProcesses.create(command);
        processBuilder.directory(workDir.toFile());
        processBuilder.redirectErrorStream(true);
        Process process = processBuilder.start();
        String output;
        try (InputStream inputStream = process.getInputStream()) {
            output = new String(inputStream.readAllBytes());
        }
        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IllegalStateException("Command failed (exit=" + exitCode + "): " + String.join(" ", command) + "\n" + output);
        }
    }
}
