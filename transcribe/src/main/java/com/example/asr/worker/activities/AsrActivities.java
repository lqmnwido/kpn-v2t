package com.example.asr.worker.activities;

import com.example.asr.worker.model.ArtifactPaths;
import com.example.asr.worker.model.ChunkInfo;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

import java.util.List;

@ActivityInterface
public interface AsrActivities {

    @ActivityMethod
    String transcribeChunk(ChunkInfo chunkInfo, String jobId, String bucket);

    @ActivityMethod
    String transcribePreparedChunk(ChunkInfo chunkInfo, String jobId, String bucket, String timelineKey);

    @ActivityMethod
    String transcribeChunkWithDiarization(ChunkInfo chunkInfo, String diarizationJson, String jobId, String bucket);

    @ActivityMethod
    ArtifactPaths mergeOutputs(List<String> chunkTranscriptKeys, String jobId, String bucket);

    @ActivityMethod
    void finalizeRefinement(String jobId, String transcriptKey, String bucket);
    }
