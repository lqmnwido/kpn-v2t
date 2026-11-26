package com.example.asr.worker.activities;

import com.example.asr.worker.config.WorkerProcesses;
import org.springframework.core.env.StandardEnvironment;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.minio.GetObjectArgs;
import io.minio.GetObjectResponse;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import okhttp3.Headers;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.ByteArrayInputStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class MediaActivitiesImplTest {
    @TempDir
    Path directory;

    @Test
    void normalizationPublishesSeparateAsrListeningAndMeasurementArtifacts() throws Exception {
        Path ffmpeg = Path.of("/usr/bin/ffmpeg");
        Path ffprobe = Path.of("/usr/bin/ffprobe");
        Path python = Path.of("python/venv/bin/python").toAbsolutePath();
        assumeTrue(Files.isExecutable(ffmpeg) && Files.isExecutable(ffprobe) && Files.isExecutable(python));
        Path fixture = directory.resolve("fixture.wav");
        var generation = new ProcessBuilder(ffmpeg.toString(), "-hide_banner", "-v", "error",
                "-f", "lavfi", "-i", "sine=frequency=300:duration=4", fixture.toString()).start();
        assertEquals(0, generation.waitFor());
        byte[] original = Files.readAllBytes(fixture);
        MinioClient storage = mock(MinioClient.class);
        when(storage.getObject(any(GetObjectArgs.class))).thenAnswer(invocation ->
                new GetObjectResponse(new Headers.Builder().build(), "bucket", "region", "source.wav",
                        new ByteArrayInputStream(original)));
        Map<String, byte[]> uploaded = new HashMap<>();
        doAnswer(invocation -> {
            PutObjectArgs args = invocation.getArgument(0);
            assertEquals("bucket", args.bucket());
            uploaded.put(args.object(), args.stream().readAllBytes());
            return null;
        }).when(storage).putObject(any(PutObjectArgs.class));
        Path current = Path.of("").toAbsolutePath();
        String workingDirectory = current.getRoot().equals(directory.getRoot())
                ? current.relativize(directory).toString() : directory.toString();
        MediaActivitiesImpl media = new MediaActivitiesImpl(storage, "bucket", ffmpeg.toString(),
                ffprobe.toString(), workingDirectory, 30, "auto", python.toString(),
                new WorkerProcesses(new StandardEnvironment()));

        assertEquals("normalized/job/audio.wav", media.validateAndNormalize("uploads/source.wav", "job", "bucket"));
        assertEquals(3, uploaded.size());
        assertTrue(uploaded.get("normalized/job/audio.wav").length > 44);
        assertTrue(uploaded.get("normalized/job/audio-enhanced.m4a").length > 0);
        var report = new ObjectMapper().readTree(uploaded.get("normalized/job/audio-enhancement.json"));
        assertEquals("speech-cleanup-v1", report.get("policy").asText());
        assertEquals(16000, report.at("/outputs/asr/sample_rate").asInt());
        assertArrayEquals(original, Files.readAllBytes(directory.resolve("job/source.wav")));
    }
}
