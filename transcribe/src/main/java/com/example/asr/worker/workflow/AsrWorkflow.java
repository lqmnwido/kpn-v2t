package com.example.asr.worker.workflow;

import com.example.asr.worker.model.StartAsrRequest;
import com.example.asr.worker.model.WorkflowJobStatus;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface AsrWorkflow {
    @WorkflowMethod
    void start(StartAsrRequest request);

    @QueryMethod
    WorkflowJobStatus getStatus();

    @SignalMethod
    void wakeUp();
}