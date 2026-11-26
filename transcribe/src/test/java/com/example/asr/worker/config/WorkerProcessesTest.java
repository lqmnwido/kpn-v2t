package com.example.asr.worker.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.context.config.ConfigDataEnvironmentPostProcessor;
import org.springframework.core.env.MapPropertySource;
import org.springframework.core.env.StandardEnvironment;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;

class WorkerProcessesTest {
    @Test
    void forwardsImportedDotenvValuesWithSpringPrecedence(@TempDir Path directory) throws Exception {
        Path dotenv = directory.resolve(".env");
        Files.writeString(dotenv, "ASR_LANGUAGE=ms\nWHISPER_MODEL=fixture\n");
        var environment = new StandardEnvironment();
        environment.getPropertySources().addFirst(new MapPropertySource("test", Map.of(
                "spring.config.import", dotenv.toUri() + "[.properties]",
                "ASR_LANGUAGE", "auto",
                "TEST_SPRING_ONLY", "private")));
        ConfigDataEnvironmentPostProcessor.applyTo(environment);

        assertEquals("fixture", environment.getProperty("WHISPER_MODEL"));
        var processEnvironment = new WorkerProcesses(environment).create(List.of("python")).environment();
        assertEquals("fixture", processEnvironment.get("WHISPER_MODEL"));
        assertEquals("auto", processEnvironment.get("ASR_LANGUAGE"));
        assertFalse(processEnvironment.containsKey("TEST_SPRING_ONLY"));
    }
}
