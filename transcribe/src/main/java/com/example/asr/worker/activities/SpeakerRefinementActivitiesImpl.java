package com.example.asr.worker.activities;

import com.example.asr.worker.config.WorkerProcesses;

import io.temporal.spring.boot.ActivityImpl;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

@Component("speakerRefinementActivitiesImpl")
@ActivityImpl(workers = { "asrWorker" })
public class SpeakerRefinementActivitiesImpl implements SpeakerRefinementActivities {

    private final String minioUrl;
    private final String minioAccessKey;
    private final String minioSecretKey;
    private final String pythonExecutable;
    private final String refineScriptPath;
    private final Path workingDir;
    private final WorkerProcesses workerProcesses;

    public SpeakerRefinementActivitiesImpl(
            @Value("${app.minio.url}") String minioUrl,
            @Value("${app.minio.access-key}") String minioAccessKey,
            @Value("${app.minio.secret-key}") String minioSecretKey,
            @Value("${app.tools.python-executable:python}") String pythonExecutable,
            @Value("${app.tools.refine-script-path}") String refineScriptPath,
            @Value("${app.tools.working-dir}") String workingDir,
            WorkerProcesses workerProcesses) {
        this.minioUrl = minioUrl;
        this.minioAccessKey = minioAccessKey;
        this.minioSecretKey = minioSecretKey;
        this.pythonExecutable = Path.of(pythonExecutable).toAbsolutePath().toString();
        this.refineScriptPath = Path.of(refineScriptPath).toAbsolutePath().toString();
        this.workerProcesses = workerProcesses;
        this.workingDir = Path.of(workingDir).toAbsolutePath().normalize();
    }

    @Override
    public String refineSpeakers(String jobId, String transcriptKey, String bucket) {
        List<String> command = createBaseCommand(refineScriptPath, jobId, transcriptKey, bucket);
        try {
            return runCommand(command, workingDir);
        } catch (Exception e) {
            return transcriptKey;
        }
    }

    private List<String> createBaseCommand(String script, String jobId, String transcriptKey, String bucket) {
        List<String> command = new ArrayList<>();
        command.add(pythonExecutable);
        command.add(script);
        command.add("--job-id");
        command.add(jobId);
        if (transcriptKey != null) {
            command.add("--transcript-key");
            command.add(transcriptKey);
        }
        command.add("--minio-url");
        command.add(minioUrl);
        command.add("--minio-access-key");
        command.add(minioAccessKey);
        command.add("--minio-secret-key");
        command.add(minioSecretKey);
        command.add("--minio-bucket");
        command.add(bucket);
        return command;
    }

    private String runCommand(List<String> command, Path workDir) throws Exception {
        ProcessBuilder processBuilder = workerProcesses.create(command);
        processBuilder.directory(workDir.toFile());
        processBuilder.redirectErrorStream(true);
        Process process = processBuilder.start();
        String output;
        try (InputStream inputStream = process.getInputStream()) {
            output = new String(inputStream.readAllBytes(), StandardCharsets.UTF_8);
        }
        if (process.waitFor() != 0)
            throw new IllegalStateException(output);
        String[] lines = output.split("\\R");
        return lines[lines.length - 1].trim();
    }
}
