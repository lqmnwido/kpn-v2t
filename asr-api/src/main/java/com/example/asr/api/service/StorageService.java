package com.example.asr.api.service;

import io.minio.BucketExistsArgs;
import io.minio.MakeBucketArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.minio.StatObjectArgs;
import io.minio.errors.ErrorResponseException;
import jakarta.annotation.PostConstruct;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.multipart.MultipartFile;

import java.io.InputStream;
import java.util.UUID;

@Service
public class StorageService {

    private final MinioClient minioClient;
    private final String bucket;

    public StorageService(MinioClient minioClient,
                          @Value("${app.minio.bucket}") String bucket) {
        this.minioClient = minioClient;
        this.bucket = bucket;
    }

    @PostConstruct
    public void ensureBucket() throws Exception {
        boolean exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
        if (!exists) {
            minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
        }
    }

    public String getDefaultBucket() {
        return this.bucket;
    }

    public boolean objectExists(String bucketName, String objectKey) throws Exception {
        try {
            minioClient.statObject(StatObjectArgs.builder().bucket(bucketName).object(objectKey).build());
            return true;
        } catch (ErrorResponseException e) {
            if ("NoSuchKey".equals(e.errorResponse().code())) {
                return false;
            }
            throw e;
        }
    }

    public String uploadOriginal(UUID jobId, MultipartFile file, String targetBucket) throws Exception {
        String bucketName = (targetBucket == null || targetBucket.isBlank()) ? this.bucket : targetBucket;
        String objectKey = "uploads/%s/%s".formatted(jobId, file.getOriginalFilename());
        try (InputStream inputStream = file.getInputStream()) {
            minioClient.putObject(
                    PutObjectArgs.builder()
                            .bucket(bucketName)
                            .object(objectKey)
                            .stream(inputStream, file.getSize(), -1)
                            .contentType(file.getContentType())
                            .build());
        }
        return objectKey;
    }

    public String uploadOriginal(UUID jobId, MultipartFile file) throws Exception {
        return uploadOriginal(jobId, file, null);
    }
}
