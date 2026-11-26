package com.example.asr.worker.activities;

import com.example.asr.worker.model.ChunkInfo;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface DiarizationActivities {

    @ActivityMethod
    String prepareRecording(String normalizedKey, String jobId, String bucket);

    @ActivityMethod
    String diarizeChunk(ChunkInfo chunkInfo, String segmentsJson, String jobId, String bucket);
}
