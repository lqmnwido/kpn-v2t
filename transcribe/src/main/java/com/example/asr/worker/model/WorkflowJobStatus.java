package com.example.asr.worker.model;

public record WorkflowJobStatus(
        String status,
        String currentStep,
        Integer progressPercent,
        Integer totalChunks,
        Integer completedChunks,
        String transcriptObjectKey,
        String errorMessage) {
}
