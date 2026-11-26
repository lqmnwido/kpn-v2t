package com.example.asr.worker.activities;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface SpeakerRefinementActivities {

    @ActivityMethod
    String refineSpeakers(String jobId, String transcriptKey, String bucket);
}
