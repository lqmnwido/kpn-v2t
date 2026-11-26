package com.example.asr.worker.model;

public record StartAsrRequest(String jobId, String inputObjectKey, String mediaTaskQueue, String asrTaskQueue, String targetBucket) {}
