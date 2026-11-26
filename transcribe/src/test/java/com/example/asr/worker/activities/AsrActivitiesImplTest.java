package com.example.asr.worker.activities;

import com.example.asr.worker.config.PythonSidecarManager;
import com.example.asr.worker.model.ChunkInfo;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.minio.GetObjectArgs;
import io.minio.GetObjectResponse;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import okhttp3.Headers;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.ByteArrayInputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class AsrActivitiesImplTest {
    @TempDir
    Path directory;

    private GetObjectResponse object(String text) {
        return new GetObjectResponse(new Headers.Builder().build(), "bucket", "region", "object",
                new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8)));
    }

    @Test
    void finalizationProofreadsBeforePublishingAndPreservesOriginalEvidenceAcrossRetry() throws Exception {
        MinioClient storage = mock(MinioClient.class);
        PythonSidecarManager worker = mock(PythonSidecarManager.class);
        ObjectMapper mapper = new ObjectMapper();
        Path job = Files.createDirectories(directory.resolve("job"));
        Files.write(job.resolve("audio-normalized.wav"), new byte[0]);
        Map<String, String> documents = new HashMap<>();
        documents.put("results/job/transcript.json", """
                {"jobId":"job","createdAt":"2026-10-22T00:00:00Z","segments":[
                  {"segmentId":"seg_1","start":0,"end":3,"speaker":"Speaker_7",
                   "text":"semua ahli tautan kuasa berbincang.","asr_quality":{"flags":[]}}
                ]}
                """);
        when(storage.getObject(any(GetObjectArgs.class))).thenAnswer(invocation -> {
            GetObjectArgs args = invocation.getArgument(0);
            return object(documents.getOrDefault(args.object(), "{}"));
        });
        doAnswer(invocation -> {
            PutObjectArgs args = invocation.getArgument(0);
            documents.put(args.object(), new String(args.stream().readAllBytes(), StandardCharsets.UTF_8));
            return null;
        }).when(storage).putObject(any(PutObjectArgs.class));
        when(worker.sendTask(any())).thenAnswer(invocation -> {
            Map<?, ?> task = invocation.getArgument(0);
            assertEquals("proofread_transcript", task.get("action"));
            var corrected = ((com.fasterxml.jackson.databind.node.ObjectNode) task.get("transcript")).deepCopy();
            var row = (com.fasterxml.jackson.databind.node.ObjectNode) corrected.path("segments").get(0);
            row.put("asr_text", row.path("text").asText());
            row.put("text", "semua ahli jawatankuasa berbincang.");
            corrected.putObject("proofreading").put("status", "completed").put("acceptedEdits", 1);
            var response = mapper.createObjectNode().put("status", "success");
            response.set("transcript", corrected);
            return response;
        });
        AsrActivitiesImpl activities = new AsrActivitiesImpl(storage, mapper, "bucket", worker, directory.toString());
        for (int attempt = 0; attempt < 2; attempt++) {
            activities.finalizeRefinement("job", "results/job/transcript.json", "bucket");
            var published = mapper.readTree(documents.get("results/job/transcript.json"));
            assertEquals("semua ahli jawatankuasa berbincang.", published.path("segments").get(0).path("text").asText());
            assertEquals(4, published.size());
            var process = mapper.readTree(documents.get("results/job/transcript.process.json"));
            assertEquals("semua ahli tautan kuasa berbincang.", process.path("segments").get(0).path("asr_text").asText());
            assertEquals("completed", process.path("proofreading").path("status").asText());
        }
        verify(worker, times(1)).sendTask(any());
    }

    private com.fasterxml.jackson.databind.JsonNode mergeChunks(String... chunks) throws Exception {
        MinioClient storage = mock(MinioClient.class);
        ObjectMapper mapper = new ObjectMapper();
        Map<String, String> documents = new HashMap<>();
        java.util.List<String> keys = new java.util.ArrayList<>();
        for (int index = 0; index < chunks.length; index++) {
            keys.add("chunk-" + index);
            documents.put(keys.get(index), chunks[index]);
        }
        when(storage.getObject(any(GetObjectArgs.class))).thenAnswer(invocation -> {
            GetObjectArgs args = invocation.getArgument(0);
            return object(documents.get(args.object()));
        });
        doAnswer(invocation -> {
            PutObjectArgs args = invocation.getArgument(0);
            documents.put(args.object(), new String(args.stream().readAllBytes(), StandardCharsets.UTF_8));
            return null;
        }).when(storage).putObject(any(PutObjectArgs.class));
        AsrActivitiesImpl activities = new AsrActivitiesImpl(storage, mapper, "bucket",
                mock(PythonSidecarManager.class), directory.toString());
        activities.mergeOutputs(keys, "job", "bucket");
        return mapper.readTree(documents.get("results/job/transcript.json"));
    }

    @Test
    void overlappingChunkTextIsJoinedWithoutInventingWordTimes() throws Exception {
        var data = mergeChunks("""
                [{"start":110.4,"end":113.5,"speaker":"Speaker_1","text":"apa-apa ketidakseragaman"}]
                """, """
                [{"start":112.0,"end":114.2,"speaker":"Speaker_1","text":"ketidakseragaman ataupun"}]
                """);
        var rows = data.path("segments");
        assertEquals(1, rows.size());
        assertEquals("apa-apa ketidakseragaman ataupun", rows.get(0).path("text").asText());
        assertEquals(110.4, rows.get(0).path("start").asDouble());
        assertEquals(114.2, rows.get(0).path("end").asDouble());
        assertEquals(2, rows.get(0).path("overlap_sources").size());
        assertTrue(rows.get(0).path("words").isEmpty());
    }

    @Test
    void overlappingPrefixUsesExistingWordTimestampForContinuation() throws Exception {
        var data = mergeChunks("""
                [{"start":110.4,"end":113.5,"speaker":"Speaker_1","text":"apa-apa ketidakseragaman"}]
                """, """
                [{"start":112.0,"end":114.2,"speaker":"Speaker_1","text":"ketidakseragaman ataupun",
                  "words":[{"start":112.0,"end":113.5,"word":" ketidakseragaman"},
                           {"start":113.5,"end":114.2,"word":" ataupun"}]}]
                """);
        var row = data.path("segments").get(1);
        assertEquals("ataupun", row.path("text").asText());
        assertEquals(113.5, row.path("start").asDouble());
        assertEquals(1, row.path("words").size());
        assertEquals("ketidakseragaman ataupun", row.path("overlap_original").path("text").asText());
    }

    @Test
    void genuineRepetitionsAndDifferentSpeakersAreNotDeduplicated() throws Exception {
        var sameChunk = mergeChunks("""
                [{"start":110,"end":113,"speaker":"Speaker_1","text":"betul betul"},
                 {"start":112,"end":114,"speaker":"Speaker_1","text":"betul sekali"}]
                """);
        assertEquals(2, sameChunk.path("segments").size());
        var separateTime = mergeChunks("""
                [{"start":110,"end":112,"speaker":"Speaker_1","text":"betul"}]
                """, """
                [{"start":112,"end":114,"speaker":"Speaker_1","text":"betul sekali"}]
                """);
        assertEquals(2, separateTime.path("segments").size());
        var otherSpeaker = mergeChunks("""
                [{"start":110,"end":113,"speaker":"Speaker_1","text":"betul"}]
                """, """
                [{"start":112,"end":114,"speaker":"Speaker_2","text":"betul sekali"}]
                """);
        assertEquals(2, otherSpeaker.path("segments").size());
    }

    @Test
    void preparedChunkDownloadsDurableTimelineAndArchivesPrimaryOutput() throws Exception {
        MinioClient storage = mock(MinioClient.class);
        PythonSidecarManager worker = mock(PythonSidecarManager.class);
        ObjectMapper mapper = new ObjectMapper();
        Path job = Files.createDirectories(directory.resolve("job"));
        Files.write(job.resolve("chunk-001.wav"), new byte[0]);
        when(storage.getObject(any(GetObjectArgs.class))).thenReturn(object("{\"job_id\":\"job\"}"));
        when(worker.sendTask(any())).thenAnswer(invocation -> {
            Map<?, ?> task = invocation.getArgument(0);
            assertEquals("job", task.get("job_id"));
            assertEquals("{\"job_id\":\"job\"}", Files.readString(Path.of((String) task.get("timeline_path"))));
            return mapper.readTree("""
                    {"status":"success","segments":[{"text":"Baik.","speaker":"Speaker_2"}],
                     "raw_segments":[{"text":" Baik.","raw_text":" Baik."}]}
                    """);
        });
        Map<String, String> uploads = new HashMap<>();
        doAnswer(invocation -> {
            PutObjectArgs args = invocation.getArgument(0);
            uploads.put(args.object(), new String(args.stream().readAllBytes(), StandardCharsets.UTF_8));
            return null;
        }).when(storage).putObject(any(PutObjectArgs.class));

        AsrActivitiesImpl activities = new AsrActivitiesImpl(storage, mapper, "bucket", worker, directory.toString());
        String result = activities.transcribePreparedChunk(new ChunkInfo("chunk.wav", 1, 28),
                "job", "bucket", "results/job/speaker_timeline.json");

        assertEquals("Speaker_2", mapper.readTree(result).get(0).get("speaker").asText());
        assertEquals(" Baik.", mapper.readTree(uploads.get("results/job/raw/chunk-001.json"))
                .get(0).get("raw_text").asText());
    }

    @Test
    void reviewArtifactRetainsAudioReferencesAndOnlyFlaggedSegments() throws Exception {
        MinioClient storage = mock(MinioClient.class);
        ObjectMapper mapper = new ObjectMapper();
        when(storage.getObject(any(GetObjectArgs.class))).thenReturn(object("""
                {"segments":[
                    {"segmentId":"seg_000001","startSeconds":0,"endSeconds":1,
                     "speaker":"Speaker_1","text":"Baik.","asr_quality":{"review_required":false}},
                    {"segmentId":"seg_000002","startSeconds":1,"endSeconds":2,
                     "speaker":"Unknown","text":"25 Oktober","asr_quality":{"review_required":true,
                     "flags":["verification_disagreement"]}}
                ]}
                """));
        Map<String, String> uploads = new HashMap<>();
        doAnswer(invocation -> {
            PutObjectArgs args = invocation.getArgument(0);
            uploads.put(args.object(), new String(args.stream().readAllBytes(), StandardCharsets.UTF_8));
            return null;
        }).when(storage).putObject(any(PutObjectArgs.class));

        AsrActivitiesImpl activities = new AsrActivitiesImpl(storage, mapper, "bucket",
                mock(PythonSidecarManager.class), directory.toString());
        activities.finalizeRefinement("job", "results/job/transcript.json", "bucket");

        var review = mapper.readTree(uploads.get("results/job/review.json"));
        assertEquals("normalized/job/audio.wav", review.get("sourceAudioKey").asText());
        assertEquals("needs_review", review.get("status").asText());
        assertEquals(1, review.get("segments").size());
        assertEquals("seg_000002", review.get("segments").get(0).get("segmentId").asText());
        assertTrue(uploads.get("results/job/transcript.srt").contains("25 Oktober"));
    }

    @Test
    void withheldRowsAreAbsentFromPublishedSegmentsTextSpeakersAndSubtitlesAcrossRetries() throws Exception {
        MinioClient storage = mock(MinioClient.class);
        ObjectMapper mapper = new ObjectMapper();
        Map<String, String> uploads = new HashMap<>();
        uploads.put("results/job/transcript.json", """
                {"segments":[
                  {"segmentId":"seg_1","startSeconds":0,"endSeconds":1,
                   "speaker":"Speaker_1","text":"[tidak jelas]","withheld_text":"invented words",
                   "asr_quality":{"disposition":"withheld","review_required":true}},
                  {"segmentId":"seg_2","startSeconds":2,"endSeconds":3,
                   "speaker":"Unknown","text":"[tidak jelas]"},
                  {"segmentId":"seg_3","startSeconds":4,"endSeconds":5,
                   "speaker":"Speaker_3","text":"Tengok compare dia."}
                ],"text":"old text"}
                """);
        when(storage.getObject(any(GetObjectArgs.class))).thenAnswer(invocation -> {
            GetObjectArgs args = invocation.getArgument(0);
            return object(uploads.getOrDefault(args.object(), "{}"));
        });
        doAnswer(invocation -> {
            PutObjectArgs args = invocation.getArgument(0);
            uploads.put(args.object(), new String(args.stream().readAllBytes(), StandardCharsets.UTF_8));
            return null;
        }).when(storage).putObject(any(PutObjectArgs.class));
        AsrActivitiesImpl activities = new AsrActivitiesImpl(storage, mapper, "bucket",
                mock(PythonSidecarManager.class), directory.toString());

        for (int attempt = 0; attempt < 2; attempt++) {
            activities.finalizeRefinement("job", "results/job/transcript.json", "bucket");
            var published = mapper.readTree(uploads.get("results/job/transcript.json"));
            assertEquals(1, published.path("segments").size());
            assertEquals(4, published.size());
            assertEquals(6, published.path("segments").get(0).size());
            assertFalse(published.path("segments").get(0).has("segmentId"));
            var process = mapper.readTree(uploads.get("results/job/transcript.process.json"));
            assertEquals(3, process.path("segments").size());
            assertEquals("seg_3", process.path("segments").get(2).path("segmentId").asText());
            assertEquals("invented words", process.path("segments").get(0).path("withheld_text").asText());
            assertEquals("Speaker_1: Tengok compare dia.\n", published.path("text").asText());
            assertFalse(uploads.get("results/job/transcript.srt").contains("tidak jelas"));
            assertFalse(uploads.get("results/job/transcript.vtt").contains("tidak jelas"));
            assertEquals("[\"Speaker_1\"]", mapper.readTree(uploads.get("results/job/speaker_map.json"))
                    .path("speakers").toString());
            var review = mapper.readTree(uploads.get("results/job/review.json"));
            assertEquals(2, review.path("segments").size());
            assertEquals("invented words", review.path("segments").get(0).path("withheld_text").asText());
        }
        var allWithheld = mapper.readTree(uploads.get("results/job/transcript.json"));
        ((com.fasterxml.jackson.databind.node.ObjectNode) allWithheld.path("segments").get(0))
                .put("text", "[tidak jelas]");
        uploads.put("results/job/transcript.json", mapper.writeValueAsString(allWithheld));
        activities.finalizeRefinement("job", "results/job/transcript.json", "bucket");
        var empty = mapper.readTree(uploads.get("results/job/transcript.json"));
        assertTrue(empty.path("segments").isEmpty());
        assertEquals("", empty.path("text").asText());
        assertEquals("", uploads.get("results/job/transcript.srt"));
        assertEquals("WEBVTT\n\n", uploads.get("results/job/transcript.vtt"));
        assertEquals(3, mapper.readTree(uploads.get("results/job/review.json")).path("segments").size());

        uploads.put("results/job/transcript.json", """
                {"segments":[
                  {"segmentId":"s1","startSeconds":0,"endSeconds":1,"speaker":"Unknown","text":"tengok"},
                  {"segmentId":"s2","startSeconds":1,"endSeconds":2,"speaker":"Speaker_3","text":"compare dia"},
                  {"segmentId":"s3","startSeconds":2,"endSeconds":3,"speaker":"Speaker_11","text":"projek kita"},
                  {"segmentId":"s4","startSeconds":3,"endSeconds":4,"speaker":"Speaker_3","text":"Baik"}
                ]}
                """);
        for (int attempt = 0; attempt < 2; attempt++) {
            activities.finalizeRefinement("job", "results/job/transcript.json", "bucket");
            var rows = mapper.readTree(uploads.get("results/job/transcript.json")).path("segments");
            assertEquals(4, rows.size());
            assertEquals("Unknown", rows.get(0).path("speaker").asText());
            assertEquals("Speaker_1", rows.get(1).path("speaker").asText());
            assertEquals("Speaker_2", rows.get(2).path("speaker").asText());
            assertEquals("Speaker_1", rows.get(3).path("speaker").asText());
            var process = mapper.readTree(uploads.get("results/job/transcript.process.json"));
            assertEquals("Speaker_11", process.path("segments").get(2).path("speaker").asText());
            assertEquals("Speaker_2", process.path("publishedSpeakerLabels").path("Speaker_11").asText());
        }
    }
}
