package com.example.asr.worker.activities;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.temporal.spring.boot.ActivityImpl;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

@Component("lockActivitiesImpl")
@ActivityImpl(workers = { "asrWorker" })
public class LockActivitiesImpl implements LockActivities {

    private static final Logger logger = LoggerFactory.getLogger(LockActivitiesImpl.class);

    private final Path lockFilePath;
    private final Path waitQueuePath;
    private final ObjectMapper objectMapper;

    /**
     * Stale lock timeout — if a lock file is older than this, it is considered
     * stale (e.g. from a crashed worker) and can be re-acquired.
     */
    private static final Duration LOCK_TIMEOUT = Duration.ofHours(2);

    public LockActivitiesImpl(@Value("${app.tools.working-dir}") String workingDir,
            ObjectMapper objectMapper) throws Exception {
        this.lockFilePath = Path.of(workingDir, ".asr-lock");
        this.waitQueuePath = Path.of(workingDir, ".asr-wait-queue");
        this.objectMapper = objectMapper;
        Files.createDirectories(Path.of(workingDir));
    }

    @Override
    public synchronized boolean acquireAsrLock(String jobId) {
        try {
            if (Files.exists(lockFilePath)) {
                Map<?, ?> existing = objectMapper.readValue(lockFilePath.toFile(), Map.class);
                String existingJobId = (String) existing.get("jobId");
                String acquiredAtStr = (String) existing.get("acquiredAt");

                // Same job re-acquiring (e.g. activity retry) — allow
                if (jobId.equals(existingJobId)) {
                    logger.info("ASR lock re-acquired by same job: {}", jobId);
                    return true;
                }

                // Check if the existing lock is stale
                if (acquiredAtStr != null) {
                    Instant acquiredAt = Instant.parse(acquiredAtStr);
                    long ageMinutes = Duration.between(acquiredAt, Instant.now()).toMinutes();
                    if (Duration.between(acquiredAt, Instant.now()).compareTo(LOCK_TIMEOUT) > 0) {
                        logger.warn("ASR lock is stale ({} min old, held by {}). Re-acquiring for {}.",
                                ageMinutes, existingJobId, jobId);
                        writeLock(jobId);
                        return true;
                    }
                    logger.info("ASR lock held by {}. Age: {} min. Cannot acquire for {}.",
                            existingJobId, ageMinutes, jobId);
                } else {
                    logger.info("ASR lock held by {}. Cannot acquire for {}.", existingJobId, jobId);
                }
                return false;
            }

            // No lock file — acquire
            logger.info("ASR lock acquired for job: {}", jobId);
            writeLock(jobId);
            return true;
        } catch (Exception e) {
            logger.error("Error acquiring ASR lock for job {}: {}", jobId, e.getMessage());
            return false;
        }
    }

    @Override
    public synchronized String releaseAsrLock(String jobId) {
        try {
            // Delete the lock file if we own it
            if (Files.exists(lockFilePath)) {
                Map<?, ?> existing = objectMapper.readValue(lockFilePath.toFile(), Map.class);
                String existingJobId = (String) existing.get("jobId");
                if (jobId.equals(existingJobId)) {
                    Files.deleteIfExists(lockFilePath);
                    logger.info("ASR lock released for job: {}", jobId);
                } else {
                    logger.warn("Cannot release ASR lock: held by {}, requested by {}",
                            existingJobId, jobId);
                    return null;
                }
            } else {
                logger.warn("Cannot release ASR lock: no lock file exists (job {})", jobId);
                return null;
            }

            // Pop the next waiting job from the queue
            return popNextFromQueue();
        } catch (Exception e) {
            logger.error("Error releasing ASR lock for job {}: {}", jobId, e.getMessage());
            return null;
        }
    }

    @Override
    public synchronized void registerForLock(String jobId, String workflowId) {
        try {
            List<Map<String, String>> queue = readQueue();
            // Avoid duplicates
            boolean exists = queue.stream().anyMatch(e -> jobId.equals(e.get("jobId")));
            if (!exists) {
                queue.add(Map.of("jobId", jobId, "workflowId", workflowId, "registeredAt", Instant.now().toString()));
                writeQueue(queue);
                logger.info("Job {} registered in ASR wait queue (workflow: {}). Queue size: {}",
                        jobId, workflowId, queue.size());
            } else {
                logger.info("Job {} already in ASR wait queue", jobId);
            }
        } catch (Exception e) {
            logger.error("Error registering for lock: {}", e.getMessage());
        }
    }

    @Override
    public synchronized void unregisterFromLock(String jobId, String workflowId) {
        try {
            List<Map<String, String>> queue = readQueue();
            boolean removed = queue.removeIf(e -> jobId.equals(e.get("jobId")));
            if (removed) {
                writeQueue(queue);
                logger.info("Job {} removed from ASR wait queue. Queue size: {}", jobId, queue.size());
            }
        } catch (Exception e) {
            logger.error("Error unregistering from lock: {}", e.getMessage());
        }
    }

    /**
     * Pops the first entry from the wait queue and returns its workflowId.
     */
    private String popNextFromQueue() throws Exception {
        List<Map<String, String>> queue = readQueue();
        if (queue.isEmpty()) {
            logger.info("No waiting jobs in ASR queue");
            return null;
        }
        Map<String, String> next = queue.remove(0);
        writeQueue(queue);
        String nextWorkflowId = next.get("workflowId");
        logger.info("Popped next ASR waiter: job {} (workflow {}). Queue size: {}",
                next.get("jobId"), nextWorkflowId, queue.size());
        return nextWorkflowId;
    }

    private List<Map<String, String>> readQueue() throws Exception {
        if (!Files.exists(waitQueuePath)) {
            return new ArrayList<>();
        }
        String content = Files.readString(waitQueuePath);
        if (content.isBlank()) {
            return new ArrayList<>();
        }
        return objectMapper.readValue(content, new TypeReference<List<Map<String, String>>>() {
        });
    }

    private void writeQueue(List<Map<String, String>> queue) throws Exception {
        if (queue.isEmpty()) {
            Files.deleteIfExists(waitQueuePath);
        } else {
            Files.writeString(waitQueuePath, objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(queue));
        }
    }

    private void writeLock(String jobId) throws Exception {
        Map<String, Object> content = Map.of(
                "jobId", jobId,
                "acquiredAt", Instant.now().toString());
        Files.writeString(lockFilePath, objectMapper.writeValueAsString(content));
    }
}
