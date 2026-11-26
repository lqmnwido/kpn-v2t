package com.example.asr.worker.activities;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface LockActivities {

    @ActivityMethod
    boolean acquireAsrLock(String jobId);

    /**
     * Releases the ASR lock and returns the workflow ID of the next waiting
     * job (or null if the queue is empty).
     */
    @ActivityMethod
    String releaseAsrLock(String jobId);

    /**
     * Adds this job to the wait queue for the ASR execution slot.
     */
    @ActivityMethod
    void registerForLock(String jobId, String workflowId);

    /**
     * Removes this job from the wait queue (used when unblocking after a
     * stale-signal timeout).
     */
    @ActivityMethod
    void unregisterFromLock(String jobId, String workflowId);
}
