package com.example.asr.worker.config;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.annotation.PostConstruct;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.*;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

@Component
public class PythonSidecarManager {
    private static final Logger logger = LoggerFactory.getLogger(PythonSidecarManager.class);
    private final ObjectMapper objectMapper = new ObjectMapper();

    private final WorkerProcesses workerProcesses;

    public PythonSidecarManager(WorkerProcesses workerProcesses) {
        this.workerProcesses = workerProcesses;
    }

    private Process pythonProcess;
    private BufferedWriter writer;
    private BufferedReader reader;

    @Value("${app.tools.python-executable:python}")
    private String pythonExecutable;

    @Value("${app.tools.persistent-worker-path:python/persistent_worker.py}")
    private String scriptPath;

    @PostConstruct
    public void init() {
        try {
            String fullPath = Path.of(scriptPath).toAbsolutePath().toString();
            String absPython = Path.of(pythonExecutable).toAbsolutePath().toString();
            logger.info("Starting Persistent Python Worker: {} using {}", fullPath, absPython);

            String launcher = Path.of("python/start_worker.py").toAbsolutePath().toString();
            ProcessBuilder pb = workerProcesses.create(List.of(absPython, "-u", launcher, fullPath));
            Map<String, String> env = pb.environment();
            env.put("PYTHONUTF8", "1");
            env.put("PYTHONIOENCODING", "utf-8");

            // The launcher discovers CUDA libraries in the selected environment,
            // independent of the Python minor version or CUDA wheel profile.

            pb.redirectError(ProcessBuilder.Redirect.INHERIT); 

            this.pythonProcess = pb.start();
            this.writer = new BufferedWriter(new OutputStreamWriter(pythonProcess.getOutputStream()));
            this.reader = new BufferedReader(new InputStreamReader(pythonProcess.getInputStream()));

            // Wait for "ready" signal, skipping any startup chatter/warnings
            String readySignal;
            while ((readySignal = reader.readLine()) != null) {
                logger.info("Python Worker Output: {}", readySignal);
                try {
                    JsonNode node = objectMapper.readTree(readySignal);
                    if (node.has("status") && "ready".equals(node.get("status").asText())) {
                        logger.info("Python Worker is READY");
                        break;
                    }
                } catch (Exception e) {
                    // Not JSON or not the ready signal, ignore and continue
                }
            }

            if (readySignal == null) {
                throw new RuntimeException("Python worker failed to start or never sent ready signal");
            }
        } catch (Exception e) {
            logger.error("Critical: Failed to initialize Python Sidecar! {}", e.getMessage());
            cleanup();
            throw new IllegalStateException("Python transcription worker failed to initialize", e);
        }
    }

    public synchronized JsonNode sendTask(Map<String, Object> task) {
        if (pythonProcess == null || !pythonProcess.isAlive()) {
            throw new RuntimeException("Python worker is not running!");
        }
        try {
            String json = objectMapper.writeValueAsString(task);
            writer.write(json);
            writer.newLine();
            writer.flush();

            // Read lines until we get a valid JSON response (success or error)
            String response;
            while ((response = reader.readLine()) != null) {
                try {
                    JsonNode node = objectMapper.readTree(response);
                    if (node.has("status")) {
                        String status = node.get("status").asText();
                        // Ignore "ready" status if it appears during task execution
                        if ("ready".equals(status)) {
                            logger.info("Ignoring late 'ready' signal during task execution");
                            continue;
                        }
                        return node;
                    }
                } catch (Exception e) {
                    logger.warn("Skipping non-JSON output from Python: {}", response);
                }
            }
            throw new RuntimeException("Python worker closed the pipe unexpectedly!");
        } catch (Exception e) {
            logger.error("Pipe communication failed: {}", e.getMessage());
            throw new RuntimeException("Communication with Python worker failed", e);
        }
    }

    @PreDestroy
    public void cleanup() {
        if (pythonProcess != null) {
            pythonProcess.destroy();
        }
    }
}
