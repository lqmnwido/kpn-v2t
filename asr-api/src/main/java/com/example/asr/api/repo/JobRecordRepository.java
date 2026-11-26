package com.example.asr.api.repo;

import com.example.asr.api.entity.JobRecord;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface JobRecordRepository extends JpaRepository<JobRecord, UUID> {
    Optional<JobRecord> findByWorkflowId(String workflowId);
}
