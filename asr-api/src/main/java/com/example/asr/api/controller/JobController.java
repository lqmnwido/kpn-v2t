package com.example.asr.api.controller;

import com.example.asr.api.dto.CreateJobResponse;
import com.example.asr.api.dto.ExternalJobRequest;
import com.example.asr.api.dto.JobStatusResponse;
import com.example.asr.api.dto.KpnReportStatus;
import com.example.asr.api.dto.RetryAsrRequest;
import com.example.asr.api.exception.KpnReportConflict;
import com.example.asr.api.service.TemporalJobService;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ProblemDetail;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestPart;
import org.springframework.web.bind.annotation.ResponseStatus;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.multipart.MultipartFile;

import java.util.UUID;

@RestController
@RequestMapping("/jobs")
public class JobController {

    private final TemporalJobService temporalJobService;

    public JobController(TemporalJobService temporalJobService) {
        this.temporalJobService = temporalJobService;
    }

    @PostMapping(consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    @ResponseStatus(HttpStatus.CREATED)
    public CreateJobResponse createJob(@RequestPart("file") MultipartFile file,
                                       @RequestPart(value = "bucket", required = false) String bucket) throws Exception {
        return temporalJobService.createJob(file, bucket);
    }

    @PostMapping("/external")
    @ResponseStatus(HttpStatus.CREATED)
    public CreateJobResponse startExternalJob(@RequestBody ExternalJobRequest request) {
        return temporalJobService.startJobFromExternalStorage(
                UUID.fromString(request.jobId()),
                request.path());
    }

    @GetMapping("/{jobId}")
    public JobStatusResponse getJob(@PathVariable UUID jobId) {
        return temporalJobService.getJob(jobId);
    }

    @PostMapping("/{jobId}/kpn-report")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public KpnReportStatus startKpnReport(@PathVariable UUID jobId,
                                         @RequestParam(required = false) UUID templateId,
                                         @RequestParam(required = false) String templateName) {
        return temporalJobService.startKpnReport(jobId, templateId, templateName);
    }

    @ExceptionHandler(KpnReportConflict.class)
    public ResponseEntity<ProblemDetail> kpnReportConflict(KpnReportConflict conflict) {
        return ResponseEntity.status(conflict.getStatusCode()).body(conflict.getBody());
    }

    @GetMapping("/{jobId}/kpn-report/{workflowId}")
    public KpnReportStatus getKpnReport(@PathVariable UUID jobId, @PathVariable String workflowId) {
        return temporalJobService.getKpnReport(jobId, workflowId);
    }

    @DeleteMapping("/{jobId}")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public void cancelJob(@PathVariable UUID jobId) {
        temporalJobService.cancelJob(jobId);
    }

    @PostMapping("/{jobId}/{workflowId}/regenerate-analysis")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public CreateJobResponse regenerateAnalysis(@PathVariable UUID jobId, @PathVariable String workflowId) {
        return temporalJobService.regenerateAnalysis(jobId, workflowId);
    }

    @GetMapping("/{jobId}/{workflowId}/regenerate-analysis/status")
    public JobStatusResponse pollingRegenAnalysis(@PathVariable UUID jobId, @PathVariable String workflowId) {
        return temporalJobService.getWorkflowStatus(jobId, workflowId);
    }

    @PostMapping("/retry-asr")
    @ResponseStatus(HttpStatus.ACCEPTED)
    public CreateJobResponse retryAsr(@RequestBody RetryAsrRequest request) {
        return temporalJobService.retryAsr(request);
    }

    @GetMapping("/{jobId}/{workflowId}/retry-asr/status")
    public JobStatusResponse pollingRetryAsr(@PathVariable UUID jobId, @PathVariable String workflowId) {
        return temporalJobService.getWorkflowStatus(jobId, workflowId);
    }
}
