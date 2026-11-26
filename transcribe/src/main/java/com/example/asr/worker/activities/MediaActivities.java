package com.example.asr.worker.activities;

import com.example.asr.worker.model.ChunkInfo;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

import java.util.List;

@ActivityInterface
public interface MediaActivities {

    @ActivityMethod
    String validateAndNormalize(String inputObjectKey, String jobId, String bucket);

    @ActivityMethod
    List<ChunkInfo> chunkAudio(String normalizedObjectKey, String jobId, String bucket);
}