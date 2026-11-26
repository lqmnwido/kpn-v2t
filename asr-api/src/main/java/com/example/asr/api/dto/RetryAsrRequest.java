package com.example.asr.api.dto;

public record RetryAsrRequest(String jobId, String path, String workflowID) {
}
