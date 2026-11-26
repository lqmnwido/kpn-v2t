package com.example.asr.api.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;

import java.time.Instant;
import java.util.UUID;

@Entity
@Table(name = "asr_job")
public class JobRecord {

    @Id
    private UUID id;

    @Column(nullable = false, unique = true)
    private String workflowId;

    @Column(nullable = false)
    private String status;

    @Column(nullable = false)
    private String inputObjectKey;

    private String targetBucket;
    private String transcriptObjectKey;
    private String currentStep;
    private Integer progressPercent;
    private Integer totalChunks;
    private Integer completedChunks;

    @Column(length = 4000)
    private String errorMessage;

    @Column(nullable = false)
    private Instant createdAt;

    @Column(nullable = false)
    private Instant updatedAt;

    @PrePersist
    public void prePersist() {
        Instant now = Instant.now();
        this.createdAt = now;
        this.updatedAt = now;
    }

    @PreUpdate
    public void preUpdate() {
        this.updatedAt = Instant.now();
    }

    public UUID getId() { return id; }
    public void setId(UUID id) { this.id = id; }
    public String getWorkflowId() { return workflowId; }
    public void setWorkflowId(String workflowId) { this.workflowId = workflowId; }
    public String getStatus() { return status; }
    public void setStatus(String status) { this.status = status; }
    public String getInputObjectKey() { return inputObjectKey; }
    public void setInputObjectKey(String inputObjectKey) { this.inputObjectKey = inputObjectKey; }
    public String getTargetBucket() { return targetBucket; }
    public void setTargetBucket(String targetBucket) { this.targetBucket = targetBucket; }
    public String getTranscriptObjectKey() { return transcriptObjectKey; }
    public void setTranscriptObjectKey(String transcriptObjectKey) { this.transcriptObjectKey = transcriptObjectKey; }
    public String getCurrentStep() { return currentStep; }
    public void setCurrentStep(String currentStep) { this.currentStep = currentStep; }
    public Integer getProgressPercent() { return progressPercent; }
    public void setProgressPercent(Integer progressPercent) { this.progressPercent = progressPercent; }
    public Integer getTotalChunks() { return totalChunks; }
    public void setTotalChunks(Integer totalChunks) { this.totalChunks = totalChunks; }
    public Integer getCompletedChunks() { return completedChunks; }
    public void setCompletedChunks(Integer completedChunks) { this.completedChunks = completedChunks; }
    public String getErrorMessage() { return errorMessage; }
    public void setErrorMessage(String errorMessage) { this.errorMessage = errorMessage; }
    public Instant getCreatedAt() { return createdAt; }
    public Instant getUpdatedAt() { return updatedAt; }
}
