package com.example.asr.api.dto;

import java.time.Instant;
import java.util.UUID;

public record JobStatusResponse(
        UUID jobId,
        String workflowId,
        String status,
        String currentStep,
        Integer progressPercent,
        Integer totalChunks,
        Integer completedChunks,
        String inputObjectKey,
        String transcriptObjectKey,
        String errorMessage,
        Instant createdAt,
        Instant updatedAt) {
}
