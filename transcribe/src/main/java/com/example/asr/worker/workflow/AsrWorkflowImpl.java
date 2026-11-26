package com.example.asr.worker.workflow;

import com.example.asr.worker.activities.AsrActivities;
import com.example.asr.worker.activities.DiarizationActivities;
import com.example.asr.worker.activities.LockActivities;
import com.example.asr.worker.activities.MediaActivities;
import com.example.asr.worker.activities.SpeakerRefinementActivities;
import com.example.asr.worker.model.ArtifactPaths;
import com.example.asr.worker.model.ChunkInfo;
import com.example.asr.worker.model.StartAsrRequest;
import com.example.asr.worker.model.WorkflowJobStatus;
import io.temporal.activity.ActivityOptions;
import io.temporal.api.enums.v1.ParentClosePolicy;
import io.temporal.common.RetryOptions;
import io.temporal.failure.ChildWorkflowFailure;
import io.temporal.spring.boot.WorkflowImpl;
import io.temporal.workflow.ChildWorkflowOptions;
import io.temporal.workflow.Workflow;
import org.slf4j.Logger;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

@WorkflowImpl(workers = { "workflowWorker" })
public class AsrWorkflowImpl implements AsrWorkflow {

        private final Logger logger = Workflow.getLogger(AsrWorkflowImpl.class);

        private String status = "CREATED";
        private String currentStep = "INITIALIZED";
        private Integer progressPercent = 0;
        private Integer totalChunks = 0;
        private Integer completedChunks = 0;
        private String transcriptObjectKey;
        private String errorMessage;

        /**
         * Set to true when a {@code wakeUp} signal is received from another workflow.
         */
        private boolean wakeUpReceived = false;

        @Override
        public void wakeUp() {
                logger.info("Received wakeUp signal");
                wakeUpReceived = true;
        }

        @Override
        public void start(StartAsrRequest request) {
                MediaActivities mediaActivities = Workflow.newActivityStub(
                                MediaActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.mediaTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofMinutes(30))
                                                .setRetryOptions(RetryOptions.newBuilder()
                                                                .setMaximumAttempts(3)
                                                                .setInitialInterval(Duration.ofSeconds(2))
                                                                .build())
                                                .build());

                AsrActivities asrActivities = Workflow.newActivityStub(
                                AsrActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.asrTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofMinutes(30))
                                                .setRetryOptions(RetryOptions.newBuilder()
                                                                .setMaximumAttempts(3)
                                                                .setInitialInterval(Duration.ofSeconds(2))
                                                                .build())
                                                .build());

                DiarizationActivities diarizationActivities = Workflow.newActivityStub(
                                DiarizationActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.asrTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofMinutes(30))
                                                .setRetryOptions(RetryOptions.newBuilder()
                                                                .setMaximumAttempts(3)
                                                                .setInitialInterval(Duration.ofSeconds(2))
                                                                .build())
                                                .build());

                DiarizationActivities recordingDiarization = Workflow.newActivityStub(
                                DiarizationActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.asrTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofHours(2))
                                                .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(2).build())
                                                .build());

                DiarizationActivities windowedRecordingDiarization = Workflow.newActivityStub(
                                DiarizationActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.asrTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofHours(4))
                                                .setRetryOptions(RetryOptions.newBuilder().setMaximumAttempts(2).build())
                                                .build());

                SpeakerRefinementActivities speakerRefinementActivities = Workflow.newActivityStub(
                                SpeakerRefinementActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.asrTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofMinutes(30))
                                                .setRetryOptions(RetryOptions.newBuilder()
                                                                .setMaximumAttempts(3)
                                                                .setInitialInterval(Duration.ofSeconds(2))
                                                                .build())
                                                .build());

                LockActivities lockActivities = Workflow.newActivityStub(
                                LockActivities.class,
                                ActivityOptions.newBuilder()
                                                .setTaskQueue(request.asrTaskQueue())
                                                .setStartToCloseTimeout(Duration.ofMinutes(5))
                                                .setRetryOptions(RetryOptions.newBuilder()
                                                                .setMaximumAttempts(3)
                                                                .setInitialInterval(Duration.ofSeconds(2))
                                                                .build())
                                                .build());

                try {
                        status = "PROCESSING";

                        // --- Global ASR lock: only one ASR job proceeds at a time ---
                        String workflowId = Workflow.getInfo().getWorkflowId();
                        currentStep = "WAITING";
                        progressPercent = 0;

                        if (!lockActivities.acquireAsrLock(request.jobId())) {
                                // Lock is held by another job — register in the wait queue,
                                // then block until we receive a wakeUp signal (zero timer events).
                                logger.info("Job {} registering for ASR execution slot (workflow {})...",
                                                request.jobId(), workflowId);
                                lockActivities.registerForLock(request.jobId(), workflowId);

                                // Await the signal. Safety timeout prevents infinite wait if
                                // the signal is somehow lost (e.g. worker crash + queue corruption).
                                Workflow.await(Duration.ofHours(24), () -> wakeUpReceived);

                                // Retry acquire (the signaler should have released the lock).
                                if (!lockActivities.acquireAsrLock(request.jobId())) {
                                        // Signal was likely stale — unregister and try a fresh acquire.
                                        lockActivities.unregisterFromLock(request.jobId(), workflowId);
                                        // Fall back to polling as a safety net (rare).
                                        while (!lockActivities.acquireAsrLock(request.jobId())) {
                                                Workflow.sleep(Duration.ofSeconds(30));
                                        }
                                }
                        }

                        wakeUpReceived = false; // Reset for next usage (if any)
                        logger.info("Job {} acquired ASR lock, proceeding with processing.", request.jobId());

                        currentStep = "NORMALIZING";
                        progressPercent = 5;
                        logger.info("Starting workflow for job {}", request.jobId());

                        String normalizedKey = mediaActivities.validateAndNormalize(request.inputObjectKey(),
                                        request.jobId(),
                                        request.targetBucket());

                        // Keep histories created before this pipeline change replayable.
                        int pipelineVersion = Workflow.getVersion("recording-speaker-timeline",
                                        Workflow.DEFAULT_VERSION, 2);
                        String timelineKey = null;
                        if (pipelineVersion >= 1) {
                                currentStep = "NORMALIZING";
                                DiarizationActivities preparation = pipelineVersion >= 2
                                                ? windowedRecordingDiarization : recordingDiarization;
                                timelineKey = preparation.prepareRecording(normalizedKey,
                                                request.jobId(), request.targetBucket());
                        }

                        currentStep = "CHUNKING";
                        progressPercent = 10;
                        List<ChunkInfo> chunks = mediaActivities.chunkAudio(normalizedKey, request.jobId(),
                                        request.targetBucket());
                        totalChunks = chunks.size();
                        completedChunks = 0;

                        currentStep = "TRANSCRIBING";
                        List<String> chunkTranscripts = new ArrayList<>();
                        for (int i = 0; i < chunks.size(); i++) {
                                ChunkInfo chunk = chunks.get(i);

                                // Transcribing Phase
                                currentStep = "TRANSCRIBING";
                                String segmentsJson = timelineKey == null
                                                ? asrActivities.transcribeChunk(chunk, request.jobId(), request.targetBucket())
                                                : asrActivities.transcribePreparedChunk(chunk, request.jobId(),
                                                                request.targetBucket(), timelineKey);
                                // Update progress: Transcribe contributes 30% total
                                int transcribedProgress = Math.min(30, ((i + 1) * 30) / Math.max(totalChunks, 1));
                                int diarizedProgress = Math.min(30, (i * 30) / Math.max(totalChunks, 1));
                                progressPercent = 10 + transcribedProgress + diarizedProgress;

                                // Diarizing Phase
                                currentStep = "DIARIZING";
                                chunkTranscripts.add(
                                                diarizationActivities.diarizeChunk(chunk, segmentsJson, request.jobId(),
                                                                request.targetBucket()));
                                // Update progress: Diarize contributes 30% total
                                diarizedProgress = Math.min(30, ((i + 1) * 30) / Math.max(totalChunks, 1));
                                progressPercent = 10 + transcribedProgress + diarizedProgress;

                                completedChunks = i + 1;
                        }

                        currentStep = "MERGING";
                        progressPercent = 75;
                        ArtifactPaths artifactPaths = asrActivities.mergeOutputs(chunkTranscripts, request.jobId(),
                                        request.targetBucket());
                        transcriptObjectKey = artifactPaths.transcriptJsonKey();

                        currentStep = "REFINING_SPEAKERS";
                        progressPercent = 80;
                        transcriptObjectKey = speakerRefinementActivities.refineSpeakers(request.jobId(), transcriptObjectKey,
                                        request.targetBucket());

                        // Finalize refinement: Update SRT/VTT/SpeakerMap and delete chunks
                        asrActivities.finalizeRefinement(request.jobId(), transcriptObjectKey, request.targetBucket());

                        // Release the ASR lock and get the next waiter's workflowId
                        String nextWaiterWorkflowId = lockActivities.releaseAsrLock(request.jobId());

                        // Signal the next queued workflow to wake up
                        if (nextWaiterWorkflowId != null) {
                                logger.info("Signalling next ASR waiter: workflow {}", nextWaiterWorkflowId);
                                AsrWorkflow nextWorkflow = Workflow.newExternalWorkflowStub(AsrWorkflow.class,
                                                nextWaiterWorkflowId);
                                nextWorkflow.wakeUp();
                        }

                        // Preserve replay for histories that already started the former automatic report.
                        if (isKertas01(request.inputObjectKey())
                                        && Workflow.getVersion("kpn-rapporteur-report", Workflow.DEFAULT_VERSION, 2) == 1) {
                                startRapporteurReport(request);
                        }

                        status = "COMPLETED";
                        currentStep = "DONE";
                        progressPercent = 100;
                } catch (Exception e) {
                        // Shield cleanup from workflow cancellation — otherwise the cleanup
                        // activity calls themselves get cancelled and the lock stays held.
                        Workflow.newDetachedCancellationScope(() -> {
                                try {
                                        lockActivities.unregisterFromLock(request.jobId(),
                                                        Workflow.getInfo().getWorkflowId());
                                } catch (Exception ignored) {
                                }
                                try {
                                        String nextWaiter = lockActivities.releaseAsrLock(request.jobId());
                                        if (nextWaiter != null) {
                                                AsrWorkflow nextWorkflow = Workflow
                                                                .newExternalWorkflowStub(AsrWorkflow.class, nextWaiter);
                                                nextWorkflow.wakeUp();
                                        }
                                } catch (Exception ignored) {
                                }
                        }).run();

                        if (e.getMessage() != null && e.getMessage().contains("Activity canceled")) {
                                status = "CANCELLED";
                                currentStep = "CANCELLED";
                                errorMessage = "Workflow was cancelled";
                        } else {
                                status = "FAILED";
                                currentStep = "FAILED";
                                errorMessage = e.getMessage();
                        }
                        throw e;
                }
        }

        private static boolean isKertas01(String key) {
                String filename = key.substring(key.lastIndexOf('/') + 1);
                return key.startsWith("kpn/kertas-01/")
                                || filename.matches("(?i)Rapporteur_Kertas_01\\.[^./]+");
        }

        private void startRapporteurReport(StartAsrRequest request) {
                String runId = Workflow.getInfo().getRunId();
                String reportKey = "results/%s/kpn/%s/Rapporteur_Kertas_01.docx".formatted(request.jobId(), runId);
                var reportWorkflow = Workflow.newUntypedChildWorkflowStub("KpnRapporteurReport",
                                ChildWorkflowOptions.newBuilder()
                                                .setWorkflowId("kpn-output-" + runId)
                                                .setTaskQueue("KPN_OUTPUT_QUEUE")
                                                .setParentClosePolicy(ParentClosePolicy.PARENT_CLOSE_POLICY_ABANDON)
                                                .build());
                try {
                        reportWorkflow.executeAsync(String.class, request.targetBucket(), transcriptObjectKey, reportKey);
                        reportWorkflow.getExecution().get();
                } catch (ChildWorkflowFailure e) {
                        logger.error("Could not start KPN report for job {}", request.jobId(), e);
                }
        }

        @Override
        public WorkflowJobStatus getStatus() {
                return new WorkflowJobStatus(
                                status,
                                currentStep,
                                progressPercent,
                                totalChunks,
                                completedChunks,
                                transcriptObjectKey,
                                errorMessage);
        }
}
