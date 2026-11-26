package com.example.asr.worker.activities;

import com.example.asr.worker.config.PythonSidecarManager;
import com.example.asr.worker.model.ArtifactPaths;
import com.example.asr.worker.model.ChunkInfo;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.minio.*;
import io.minio.messages.Item;
import io.temporal.spring.boot.ActivityImpl;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.Locale;
import java.util.regex.Pattern;

@Component("asrActivitiesImpl")
@ActivityImpl(workers = { "asrWorker" })
public class AsrActivitiesImpl implements AsrActivities {

    private static final Logger logger = LoggerFactory.getLogger(AsrActivitiesImpl.class);

    private final MinioClient minioClient;
    private final ObjectMapper objectMapper;
    private final String bucket;
    private final PythonSidecarManager sidecarManager;
    private final Path workingDir;

    public AsrActivitiesImpl(MinioClient minioClient,
            ObjectMapper objectMapper,
            @Value("${app.minio.bucket}") String bucket,
            PythonSidecarManager sidecarManager,
            @Value("${app.tools.working-dir}") String workingDir) {
        this.minioClient = minioClient;
        this.objectMapper = objectMapper;
        this.bucket = bucket;
        this.sidecarManager = sidecarManager;
        this.workingDir = Path.of(workingDir).toAbsolutePath().normalize();
    }

    @PostConstruct
    public void ensureBucket() throws Exception {
        Files.createDirectories(workingDir);
        boolean exists = minioClient.bucketExists(BucketExistsArgs.builder().bucket(bucket).build());
        if (!exists) {
            minioClient.makeBucket(MakeBucketArgs.builder().bucket(bucket).build());
        }
    }

    @Override
    public String transcribeChunk(ChunkInfo chunkInfo, String jobId, String bucket) {
        return transcribePreparedChunk(chunkInfo, jobId, bucket, null);
    }

    @Override
    public String transcribePreparedChunk(ChunkInfo chunkInfo, String jobId, String bucket, String timelineKey) {
        try {
            Path jobDir = workingDir.resolve(jobId);
            Files.createDirectories(jobDir);

            Path chunkFile = jobDir.resolve("chunk-%03d.wav".formatted(chunkInfo.index()));
            if (!Files.exists(chunkFile)) {
                downloadObject(chunkInfo.chunkKey(), chunkFile, bucket);
            }

            logger.info("Transcribing chunk {} via Persistent Pipe-Worker", chunkInfo.index());

            Map<String, Object> task = new HashMap<>();
            task.put("action", "transcribe");
            task.put("audio_path", chunkFile.toAbsolutePath().toString());
            task.put("start_offset", chunkInfo.startOffset());
            if (timelineKey != null) {
                // Activities may execute on different hosts: the timeline is durable in MinIO.
                Path timeline = jobDir.resolve("speaker-timeline.json");
                downloadObject(timelineKey, timeline, bucket);
                task.put("timeline_path", timeline.toAbsolutePath().toString());
                task.put("job_id", jobId);
            }

            JsonNode response = sidecarManager.sendTask(task);

            if (response == null || response.get("status") == null) {
                throw new RuntimeException(
                        "Transcription failed: Sidecar returned null or missing status. Raw response: "
                                + (response != null ? response.toString() : "null"));
            }

            if (!"success".equals(response.get("status").asText())) {
                String errorMsg = (response.get("message") != null) ? response.get("message").asText()
                        : "Unknown error";
                throw new RuntimeException("Transcription failed: " + errorMsg);
            }

            JsonNode segments = response.get("segments");
            if (segments == null) {
                throw new RuntimeException(
                        "Transcription failed: 'segments' field is missing in successful response. Raw response: "
                                + response.toString());
            }

            if (response.has("raw_segments")) {
                putObject("results/%s/raw/chunk-%03d.json".formatted(jobId, chunkInfo.index()),
                        objectMapper.writeValueAsString(response.get("raw_segments")), "application/json", bucket);
            }

            return objectMapper.writeValueAsString(segments);
        } catch (Exception e) {
            throw new RuntimeException("Failed to transcribe chunk " + chunkInfo.index(), e);
        }
    }

    @Override
    public String transcribeChunkWithDiarization(ChunkInfo chunkInfo, String diarizationJson, String jobId,
            String bucket) {
        return diarizationJson;
    }

    @Override
    public ArtifactPaths mergeOutputs(List<String> chunkTranscriptKeys, String jobId, String bucket) {
        logger.info("Merging outputs for job {}", jobId);
        try {
            List<Map<String, Object>> allSegments = new ArrayList<>();
            double lastEndTime = -1.0;

            for (String chunkKey : chunkTranscriptKeys) {
                if (chunkKey == null || "null".equals(chunkKey) || chunkKey.isEmpty()) {
                    logger.warn("Skipping invalid or null chunk key: {}", chunkKey);
                    continue;
                }
                try (InputStream is = minioClient
                        .getObject(GetObjectArgs.builder().bucket(bucket).object(chunkKey).build())) {
                    JsonNode segments = objectMapper.readTree(is);
                    if (segments.isArray()) {
                        for (JsonNode segment : segments) {
                            Map<String, Object> segMap = objectMapper.convertValue(segment, Map.class);
                            segMap.put("sourceChunkKey", chunkKey);
                            double start = ((Number) segMap.get("start")).doubleValue();
                            double end = ((Number) segMap.get("end")).doubleValue();

                            // Include segment if it extends past the overlap region
                            // (chunks overlap by 2s, so segments from the next chunk may
                            // start before lastEndTime but still contain new content)
                            if (end > lastEndTime - 0.1) {
                                if (!allSegments.isEmpty()
                                        && mergeChunkBoundary(allSegments.get(allSegments.size() - 1), segMap)) {
                                    lastEndTime = Math.max(lastEndTime, end);
                                    continue;
                                }
                                start = ((Number) segMap.get("start")).doubleValue();
                                segMap.put("startSeconds", start);
                                segMap.put("endSeconds", end);
                                segMap.put("segmentId", "seg_%06d".formatted(allSegments.size() + 1));
                                allSegments.add(segMap);
                                if (end > lastEndTime) {
                                    lastEndTime = end;
                                }
                            }
                        }
                    }
                }
            }

            StringBuilder transcriptText = new StringBuilder();
            for (Map<String, Object> seg : allSegments) {
                transcriptText.append("%s: %s\n".formatted(seg.get("speaker"), seg.get("text")));
            }

            Map<String, Object> payload = new LinkedHashMap<>();
            payload.put("jobId", jobId);
            payload.put("createdAt", Instant.now().toString());
            payload.put("text", transcriptText.toString());
            payload.put("segments", allSegments);
            payload.put("sourceAudioKey", "normalized/%s/audio.wav".formatted(jobId));
            payload.put("reviewStatus", "needs_review");

            String json = objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(payload);
            // Preserve decoded text and quality evidence before global speaker refinement.
            putObject("results/%s/transcript.raw.json".formatted(jobId), json, "application/json", bucket);
            String jsonKey = putObject("results/%s/transcript.json".formatted(jobId), json, "application/json", bucket);

            return new ArtifactPaths(jsonKey, jsonKey, jsonKey);
        } catch (Exception e) {
            throw new RuntimeException("Failed to merge outputs", e);
        }
    }

    private static final Pattern TRANSCRIPT_TOKEN = Pattern.compile("[\\p{L}\\p{N}]+(?:[-’'][\\p{L}\\p{N}]+)*");

    private List<String> transcriptTokens(String text) {
        return TRANSCRIPT_TOKEN.matcher(text.toLowerCase(Locale.ROOT)).results().map(match -> match.group()).toList();
    }

    @SuppressWarnings("unchecked")
    private boolean mergeChunkBoundary(Map<String, Object> previous, Map<String, Object> current) {
        String speaker = String.valueOf(current.getOrDefault("speaker", "Unknown"));
        double previousEnd = ((Number) previous.get("end")).doubleValue();
        double currentStart = ((Number) current.get("start")).doubleValue();
        if (speaker.equalsIgnoreCase("Unknown") || !speaker.equals(previous.get("speaker"))
                || current.get("sourceChunkKey").equals(previous.get("sourceChunkKey"))
                || currentStart >= previousEnd - .05) {
            return false;
        }
        String leftText = String.valueOf(previous.get("text"));
        String rightText = String.valueOf(current.get("text"));
        if ("[tidak jelas]".equalsIgnoreCase(leftText.trim()) || "[tidak jelas]".equalsIgnoreCase(rightText.trim())) {
            return false;
        }
        List<String> left = transcriptTokens(leftText), right = transcriptTokens(rightText);
        int matched = 0;
        for (int count = Math.min(left.size(), right.size()); count > 0; count--) {
            if (left.subList(left.size() - count, left.size()).equals(right.subList(0, count))) {
                matched = count;
                break;
            }
        }
        if (matched == 0) {
            return false;
        }
        // Avoid inferring a duplicate from a single short acknowledgement.
        if (matched == 1 && right.get(0).length() < 4) {
            return false;
        }
        List<Map<String, Object>> words = (List<Map<String, Object>>) current.get("words");
        int consumed = 0, wordIndex = 0;
        if (words != null && transcriptTokens(words.stream().map(w -> String.valueOf(w.get("word")))
                .collect(java.util.stream.Collectors.joining())).equals(right)) {
            while (wordIndex < words.size() && consumed < matched) {
                consumed += transcriptTokens(String.valueOf(words.get(wordIndex++).get("word"))).size();
            }
            if (wordIndex > 0 && ((Number) words.get(wordIndex - 1).get("start")).doubleValue() >= previousEnd) {
                return false; // Timing says the repeated word was spoken later.
            }
            if (consumed == matched && wordIndex < words.size()) {
                double remainingStart = ((Number) words.get(wordIndex).get("start")).doubleValue();
                if (remainingStart >= previousEnd - .1
                        && remainingStart < ((Number) current.get("end")).doubleValue()) {
                    current.put("overlap_original", new LinkedHashMap<>(current));
                    var remaining = new ArrayList<>(words.subList(wordIndex, words.size()));
                    current.put("words", remaining);
                    current.put("text", remaining.stream().map(w -> String.valueOf(w.get("word")))
                            .collect(java.util.stream.Collectors.joining()).trim());
                    current.put("start", remainingStart);
                    return false;
                }
            }
        }
        // Without reliable word alignment, join the source spans rather than
        // inventing a timestamp for the remaining suffix.
        int prefixEnd = TRANSCRIPT_TOKEN.matcher(rightText).results().skip(matched - 1L).findFirst().orElseThrow().end();
        String suffix = rightText.substring(prefixEnd).replaceFirst("^[\\p{Punct}\\s]+", "");
        List<Map<String, Object>> sources = new ArrayList<>();
        if (previous.containsKey("overlap_sources")) {
            sources.addAll((List<Map<String, Object>>) previous.get("overlap_sources"));
        } else {
            sources.add(new LinkedHashMap<>(previous));
        }
        sources.add(new LinkedHashMap<>(current));
        previous.put("overlap_sources", sources);
        previous.put("text", suffix.isEmpty() ? leftText : leftText + " " + suffix);
        previous.put("end", Math.max(previousEnd, ((Number) current.get("end")).doubleValue()));
        previous.put("endSeconds", previous.get("end"));
        previous.put("sourceChunkKey", current.get("sourceChunkKey"));
        previous.put("words", List.of());
        Map<String, Object> quality = new LinkedHashMap<>((Map<String, Object>) previous.getOrDefault("asr_quality", Map.of()));
        Set<String> flags = new TreeSet<>((List<String>) quality.getOrDefault("flags", List.of()));
        Map<String, Object> currentQuality = (Map<String, Object>) current.getOrDefault("asr_quality", Map.of());
        flags.addAll((List<String>) currentQuality.getOrDefault("flags", List.of()));
        flags.add("word_alignment_unavailable");
        quality.put("flags", new ArrayList<>(flags));
        quality.put("review_required", true);
        previous.put("asr_quality", quality);
        return true;
    }

    @Override
    public void finalizeRefinement(String jobId, String transcriptKey, String bucket) {
        logger.info("Finalizing refinement and generating subtitles for job {}", jobId);
        try {
            InputStream is = minioClient
                    .getObject(GetObjectArgs.builder().bucket(bucket).object(transcriptKey).build());
            JsonNode data = objectMapper.readTree(is);
            String processKey = "results/%s/transcript.process.json".formatted(jobId);
            // A retry may read the already-published, minimal document. Restore
            // its metadata only if the process snapshot projects to that same
            // public content; never override edited or newly refined speech.
            boolean hasMetadata = data.has("reviewStatus");
            for (JsonNode segment : data.path("segments")) {
                hasMetadata |= segment.has("asr_quality") || segment.has("raw_text") || segment.has("segmentId");
            }
            if (!hasMetadata) {
                try (InputStream previous = minioClient.getObject(GetObjectArgs.builder().bucket(bucket)
                        .object(processKey).build())) {
                    JsonNode snapshot = objectMapper.readTree(previous);
                    if (snapshot != null && snapshot.path("segments").isArray()
                            && publicTranscript(snapshot, jobId).equals(publicTranscript(data, jobId))) {
                        data = snapshot;
                    }
                } catch (io.minio.errors.ErrorResponseException e) {
                    if (!"NoSuchKey".equals(e.errorResponse().code())) {
                        throw e;
                    }
                }
            }
            data = proofreadTranscript(data, jobId, bucket);
            JsonNode segments = data.get("segments");

            List<JsonNode> reviewSegments = new ArrayList<>();
            // Finalization can retry after the published transcript was already
            // cleaned. Keep the review evidence saved by the previous attempt.
            try (InputStream previous = minioClient.getObject(GetObjectArgs.builder().bucket(bucket)
                    .object("results/%s/review.json".formatted(jobId)).build())) {
                JsonNode previousReview = objectMapper.readTree(previous);
                if (previousReview != null && previousReview.has("status") && previousReview.path("segments").isArray()) {
                    previousReview.path("segments").forEach(reviewSegments::add);
                }
            } catch (io.minio.errors.ErrorResponseException e) {
                if (!"NoSuchKey".equals(e.errorResponse().code())) {
                    throw e;
                }
            }
            var publishedSegments = objectMapper.createArrayNode();
            Set<String> publishedSpeakers = new TreeSet<>();
            Map<String, String> speakerLabels = new LinkedHashMap<>();
            StringBuilder publishedText = new StringBuilder();
            for (JsonNode segment : segments) {
                String text = segment.path("text").asText("").trim();
                boolean withheld = unpublishedSegment(segment);
                if (withheld || segment.path("asr_quality").path("review_required").asBoolean(false)) {
                    reviewSegments.removeIf(existing -> existing.path("segmentId").equals(segment.path("segmentId"))
                            && existing.path("startSeconds").equals(segment.path("startSeconds")));
                    reviewSegments.add(segment);
                }
                if (!withheld) {
                    String speaker = publishedSpeaker(segment, speakerLabels);
                    var publicRow = publicSegment(segment);
                    publicRow.put("speaker", speaker);
                    publishedSegments.add(publicRow);
                    publishedSpeakers.add(speaker);
                    publishedText.append(speaker).append(": ").append(text).append("\n");
                }
            }
            var process = (com.fasterxml.jackson.databind.node.ObjectNode) data.deepCopy();
            process.set("publishedSpeakerLabels", objectMapper.valueToTree(speakerLabels));
            putObject(processKey, objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(process),
                    "application/json", bucket);
            Map<String, Object> review = new LinkedHashMap<>();
            review.put("jobId", jobId);
            review.put("status", "needs_review");
            review.put("sourceAudioKey", "normalized/%s/audio.wav".formatted(jobId));
            review.put("segments", reviewSegments);
            review.put("note", "Flags are review hints; unflagged segments are not verified.");
            putObject("results/%s/review.json".formatted(jobId),
                    objectMapper.writeValueAsString(review), "application/json", bucket);

            // Keep rejected rows in review/raw artifacts, never in the published
            // segments, speaker list, text or subtitles. Preserve source IDs/times.
            var published = objectMapper.createObjectNode();
            published.put("jobId", data.path("jobId").asText(jobId));
            published.put("createdAt", data.path("createdAt").asText(""));
            published.put("text", publishedText.toString());
            published.set("segments", publishedSegments);
            putObject(transcriptKey, objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(published),
                    "application/json", bucket);
            putObject("results/%s/speaker_map.json".formatted(jobId),
                    objectMapper.writeValueAsString(Map.of("jobId", jobId, "speakers", publishedSpeakers)),
                    "application/json", bucket);

            String srt = generateSrt(publishedSegments);
            putObject("results/%s/transcript.srt".formatted(jobId), srt, "text/plain", bucket);

            String vtt = generateVtt(publishedSegments);
            putObject("results/%s/transcript.vtt".formatted(jobId), vtt, "text/vtt", bucket);

        } catch (Exception e) {
            throw new RuntimeException("Failed to generate subtitles", e);
        }
    }

    private JsonNode proofreadTranscript(JsonNode data, String jobId, String bucket) {
        if ("completed".equals(data.path("proofreading").path("status").asText())) {
            return data;
        }
        boolean candidate = false;
        for (JsonNode segment : data.path("segments")) {
            candidate |= !unpublishedSegment(segment) && segment.path("analysis_eligible").asBoolean(true);
        }
        if (!candidate) {
            return data;
        }
        try {
            Path jobDir = workingDir.resolve(jobId);
            Files.createDirectories(jobDir);
            Path audio = jobDir.resolve("audio-normalized.wav");
            if (!Files.exists(audio)) {
                downloadObject("normalized/%s/audio.wav".formatted(jobId), audio, bucket);
            }
            String contextKey = "results/%s/meeting-context.txt".formatted(jobId);
            String meetingContext = "";
            try {
                var context = minioClient.statObject(StatObjectArgs.builder().bucket(bucket).object(contextKey).build());
                if (context != null && context.size() <= 64000) {
                    downloadObject(contextKey, jobDir.resolve("meeting-context.txt"), bucket);
                    meetingContext = Files.readString(jobDir.resolve("meeting-context.txt"), StandardCharsets.UTF_8);
                }
            } catch (io.minio.errors.ErrorResponseException e) {
                if (!"NoSuchKey".equals(e.errorResponse().code())) {
                    throw e;
                }
            }
            Map<String, Object> task = new LinkedHashMap<>();
            task.put("action", "proofread_transcript");
            task.put("job_id", jobId);
            task.put("audio_path", audio.toAbsolutePath().toString());
            task.put("transcript", data);
            task.put("meeting_context", meetingContext);
            JsonNode response = sidecarManager.sendTask(task);
            if (response != null && "success".equals(response.path("status").asText())
                    && response.path("transcript").isObject()) {
                return response.get("transcript");
            }
            throw new IllegalStateException("Proofreading worker did not return a transcript");
        } catch (Exception e) {
            logger.warn("Contextual proofreading unavailable for job {}: {}", jobId, e.getClass().getSimpleName());
            var result = (com.fasterxml.jackson.databind.node.ObjectNode) data.deepCopy();
            result.putObject("proofreading").put("status", "unavailable")
                    .put("reason", e.getClass().getSimpleName());
            return result;
        }
    }

    private com.fasterxml.jackson.databind.node.ObjectNode publicSegment(JsonNode segment) {
        var result = objectMapper.createObjectNode();
        double start = segment.path("start").asDouble(segment.path("startSeconds").asDouble());
        double end = segment.path("end").asDouble(segment.path("endSeconds").asDouble());
        result.put("start", start);
        result.put("end", end);
        result.put("text", segment.path("text").asText().trim());
        result.put("speaker", segment.path("speaker").asText("Unknown"));
        result.put("startSeconds", start);
        result.put("endSeconds", end);
        return result;
    }

    private com.fasterxml.jackson.databind.node.ObjectNode publicTranscript(JsonNode data, String jobId) {
        var result = objectMapper.createObjectNode();
        result.put("jobId", data.path("jobId").asText(jobId));
        result.put("createdAt", data.path("createdAt").asText(""));
        var segments = result.putArray("segments");
        Map<String, String> labels = new LinkedHashMap<>();
        for (JsonNode segment : data.path("segments")) {
            if (!unpublishedSegment(segment)) {
                var row = publicSegment(segment);
                row.put("speaker", publishedSpeaker(segment, labels));
                segments.add(row);
            }
        }
        return result;
    }

    private boolean unpublishedSegment(JsonNode segment) {
        String text = segment.path("text").asText("").trim();
        return text.isEmpty() || "[tidak jelas]".equalsIgnoreCase(text)
                || "withheld".equals(segment.path("asr_quality").path("disposition").asText());
    }

    private String publishedSpeaker(JsonNode segment, Map<String, String> labels) {
        String source = segment.path("speaker").asText("Unknown").trim();
        if (source.isEmpty() || "Unknown".equalsIgnoreCase(source)) {
            return "Unknown";
        }
        return labels.computeIfAbsent(source, key -> "Speaker_" + (labels.size() + 1));
    }

    private String generateSrt(JsonNode segments) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < segments.size(); i++) {
            JsonNode seg = segments.get(i);
            sb.append(i + 1).append("\n");
            sb.append(formatTimeSrt(seg.get("startSeconds").asDouble())).append(" --> ")
                    .append(formatTimeSrt(seg.get("endSeconds").asDouble())).append("\n");
            sb.append(seg.get("speaker").asText()).append(": ").append(seg.get("text").asText()).append("\n\n");
        }
        return sb.toString();
    }

    private String generateVtt(JsonNode segments) {
        StringBuilder sb = new StringBuilder("WEBVTT\n\n");
        for (JsonNode seg : segments) {
            sb.append(formatTimeVtt(seg.get("startSeconds").asDouble())).append(" --> ")
                    .append(formatTimeVtt(seg.get("endSeconds").asDouble())).append("\n");
            sb.append(seg.get("speaker").asText()).append(": ").append(seg.get("text").asText()).append("\n\n");
        }
        return sb.toString();
    }

    private String formatTimeSrt(double seconds) {
        long h = (long) seconds / 3600;
        long m = (long) (seconds % 3600) / 60;
        long s = (long) seconds % 60;
        long ms = (long) ((seconds * 1000) % 1000);
        return String.format("%02d:%02d:%02d,%03d", h, m, s, ms);
    }

    private String formatTimeVtt(double seconds) {
        long h = (long) seconds / 3600;
        long m = (long) (seconds % 3600) / 60;
        long s = (long) seconds % 60;
        long ms = (long) ((seconds * 1000) % 1000);
        return String.format("%02d:%02d:%02d.%03d", h, m, s, ms);
    }

    private void downloadObject(String objectKey, Path targetPath, String bucket) throws Exception {
        try (InputStream inputStream = minioClient.getObject(
                GetObjectArgs.builder().bucket(bucket).object(objectKey).build())) {
            Files.copy(inputStream, targetPath, StandardCopyOption.REPLACE_EXISTING);
        }
    }

    private String putObject(String objectKey, String content, String contentType, String bucket) throws Exception {
        byte[] bytes = content.getBytes(StandardCharsets.UTF_8);
        minioClient.putObject(PutObjectArgs.builder().bucket(bucket).object(objectKey)
                .stream(new ByteArrayInputStream(bytes), bytes.length, -1).contentType(contentType).build());
        return objectKey;
    }
}
