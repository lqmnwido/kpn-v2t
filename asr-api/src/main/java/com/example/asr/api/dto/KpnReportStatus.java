package com.example.asr.api.dto;

public record KpnReportStatus(String workflowId, String status, String bucket, String outputKey, String errorMessage, String templateId, String currentStep, Integer progressPercent) {}
