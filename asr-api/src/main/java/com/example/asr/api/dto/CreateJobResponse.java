package com.example.asr.api.dto;

import java.time.Instant;
import java.util.UUID;

public record CreateJobResponse(
        UUID jobId,
        String workflowId,
        String status,
        Instant createdAt) {
}
