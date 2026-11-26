package com.example.kpn;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import io.minio.GetObjectArgs;
import io.minio.ListObjectsArgs;
import io.minio.PutObjectArgs;
import io.minio.RemoveObjectArgs;
import io.minio.Result;
import io.minio.messages.Item;
import io.minio.GetObjectResponse;
import io.minio.MinioClient;
import okhttp3.Headers;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.util.LinkedHashMap;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class TranscriptSourceTest {
    @Test
    void usesTopLevelTextOnly() throws Exception {
        String key = "results/job/transcript.json";
        String data = """
                {"text":"Baris pertama\\nBaris kedua","segments":[{"text":"ignore me"}]}
                """;
        MinioClient storage = mock(MinioClient.class);
        when(storage.getObject(any(GetObjectArgs.class))).thenReturn(new GetObjectResponse(
                new Headers.Builder().build(), "bucket", "region", key,
                new ByteArrayInputStream(data.getBytes(StandardCharsets.UTF_8))));

        var activity = new RapporteurActivity(storage, "bucket", URI.create("http://localhost/api/chat"),
                "test", "python3");
        assertEquals("Baris pertama\nBaris kedua", activity.loadText("bucket", key));
    }

    @Test
    void windowsOverlapAndCoverEntireTranscript() {
        String text = "a".repeat(2700) + "shared evidence" + "b".repeat(4100);
        var windows = RapporteurActivity.overlappingChunks(text);
        assertEquals(3, windows.size());
        StringBuilder joined = new StringBuilder();
        for (var window : windows) {
            joined.append(window.text().substring(window.overlapWithPrevious()));
        }
        assertEquals(text, joined.toString());
        assertTrue(windows.get(0).text().contains("shared evidence"));
        assertTrue(windows.get(1).text().contains("shared evidence"));
    }

    @Test
    void savesModelWindowsAndRemovesStaleNumberedChunks() throws Exception {
        MinioClient storage = mock(MinioClient.class);
        var activity = new RapporteurActivity(storage, "bucket", URI.create("http://localhost/api/chat"),
                "test", "python3");
        var windows = RapporteurActivity.overlappingChunks("a".repeat(3201));
        var saved = new LinkedHashMap<String, JsonNode>();
        var json = new ObjectMapper();
        when(storage.putObject(any(PutObjectArgs.class))).thenAnswer(call -> {
            PutObjectArgs args = call.getArgument(0);
            assertEquals("bucket", args.bucket());
            saved.put(args.object(), json.readTree(args.stream()));
            return null;
        });
        String prefix = "kpn/job/chunk_transcript/";
        Item stale = mock(Item.class);
        when(stale.objectName()).thenReturn(prefix + "chunk_3.json");
        Item unrelated = mock(Item.class);
        when(unrelated.objectName()).thenReturn(prefix + "notes.json");
        var listed = List.of(new Result<>(stale), new Result<>(unrelated));
        when(storage.listObjects(any(ListObjectsArgs.class))).thenReturn(listed);

        activity.publishChunks("bucket", "job", windows);

        assertEquals(List.of(prefix + "chunk_1.json", prefix + "chunk_2.json"), List.copyOf(saved.keySet()));
        JsonNode second = saved.get(prefix + "chunk_2.json");
        assertEquals(windows.get(1).text(), second.path("text").textValue());
        assertEquals(600, second.path("overlapWithPrevious").intValue());
        var deletion = org.mockito.ArgumentCaptor.forClass(RemoveObjectArgs.class);
        verify(storage).removeObject(deletion.capture());
        assertEquals(prefix + "chunk_3.json", deletion.getValue().object());
    }

    @Test
    void verifiesContextAcrossSpeakerLabeledLineBreaks() {
        String transcript = """
                Speaker_1: Malaysia recognises a general
                Unknown: right to life under
                Speaker_1: Article 5 of the Federal Constitution.
                Speaker_2: Transboundary haze harms public health.
                """;
        String chunk = RapporteurActivity.overlappingChunks(transcript).getFirst().text();
        String quote = "Malaysia recognises a general right to life under Article 5 of the Federal Constitution.";
        assertFalse(chunk.contains("Speaker_"));
        assertFalse(chunk.contains("Unknown:"));
        assertEquals(quote, RapporteurActivity.verifiedExcerpt(chunk, quote, "1. Fokus utama"));
        assertNull(RapporteurActivity.verifiedExcerpt(chunk, quote.replace("Article 5", "Article 8"), "1. Fokus utama"));
    }

    @Test
    void resolvesOnlyKnownPassageIdsInTranscriptOrder() throws Exception {
        var passages = RapporteurActivity.numberedPassages(
                "The panel discussed access to clean air.\nWhat protections exist for outdoor workers?");
        var selected = new ObjectMapper().readTree("""
                ["2", "99", "1", "2", "invented statement"]
                """);
        assertEquals(List.of("The panel discussed access to clean air.",
                "What protections exist for outdoor workers?"), RapporteurActivity.resolvePassages(passages, selected));
    }

    @Test
    void keepsSeparateMalaySummaryPointsWithValidSources() throws Exception {
        var sources = List.of("Haze and climate change are mutually reinforcing.",
                "The regulation does not directly regulate ambient air quality.");
        var response = new ObjectMapper().readTree("""
                {"paragraphs":[
                  {"text":"1. Jerebu dan perubahan iklim saling memburukkan kesannya.","sourceIds":[1]},
                  {"text":"Peraturan itu tidak mengawal kualiti udara ambien secara langsung.","sourceIds":[2]},
                  {"text":"Tiada rujukan yang sah.","sourceIds":[1,9]},
                  {"text":"Haze and climate change are mutually reinforcing.","sourceIds":[1]}
                ]}
                """);
        var points = RapporteurActivity.groundedSummaries(response, sources);
        assertEquals(List.of("Jerebu dan perubahan iklim saling memburukkan kesannya.",
                "Peraturan itu tidak mengawal kualiti udara ambien secara langsung."),
                points.stream().map(RapporteurActivity.SummaryPoint::text).toList());
        assertEquals(List.of(sources.get(0)), points.get(0).excerpts());
        assertEquals(List.of(sources.get(1)), points.get(1).excerpts());
    }

    @Test
    void reportGuidanceUsesTheTemplateHeadingsInOrder() {
        String guidance = ReportGuidance.allSections();
        int cursor = -1;
        var headings = ReportTemplate.numberedHeadings();
        for (int i = 0; i < headings.size(); i++) {
            String section = ReportGuidance.forSection(i + 1);
            assertTrue(section.startsWith(headings.get(i) + "\n"));
            int position = guidance.indexOf(section);
            assertTrue(position > cursor);
            cursor = position;
        }
        assertTrue(ReportGuidance.forSection(5).contains("keutamaan jangka panjang"));
        assertTrue(ReportGuidance.forSection(6).contains("tindakan khusus"));
        assertTrue(ReportGuidance.forSection(7).contains("jangan reka jawapan"));
    }

    @Test
    void rejectsModeratorPromptsAndFirstPersonInReports() throws Exception {
        var sources = List.of("Is there anyone with any burning question that you want to ask in the room?",
                "The law does not directly regulate ambient air quality.");
        var response = new ObjectMapper().readTree("""
                {"paragraphs":[
                  {"text":"Panel menggalakkan penyertaan dalam sesi perbincangan.","sourceIds":[1]},
                  {"text":"Tidak ada orang yang ingin bertanya sesuatu dalam ruang ini.","sourceIds":[1]},
                  {"text":"Apakah ada orang yang ingin bertanya sesuatu dalam ruang ini?","sourceIds":[1]},
                  {"text":"Saya sedang berpikir tentang bagaimana untuk menghubungkan sesi sebelumnya.","sourceIds":[2]},
                  {"text":"Pembentangan menjelaskan jurang kawal selia. Kami masih meneliti kesannya.","sourceIds":[2]},
                  {"text":"Pembentangan mengenal pasti jurang kawal selia kerana peraturan sedia ada tidak mengawal kualiti udara ambien secara langsung.","sourceIds":[2]}
                ]}
                """);
        var points = RapporteurActivity.groundedSummaries(response, sources);
        assertEquals(1, points.size());
        assertTrue(points.getFirst().text().startsWith("Pembentangan mengenal pasti"));
        assertNull(RapporteurActivity.verifiedExcerpt(sources.getFirst(), sources.getFirst(), "1. Fokus utama"));
        String substantiveQuestion = "What protections exist for outdoor workers during extreme heat?";
        assertEquals(substantiveQuestion, RapporteurActivity.verifiedExcerpt(
                substantiveQuestion, substantiveQuestion, "7. Input sesi soal jawab"));
    }

    @ParameterizedTest
    @ValueSource(booleans = {false, true})
    @SuppressWarnings("unchecked")
    void recoversDistinctParagraphsFromSparseOrIncompleteSummaries(boolean truncated) throws Exception {
        var json = new ObjectMapper();
        String evidence = """
                ["The climate panel examined haze.", "The panel also discussed public health."]
                """;
        MinioClient storage = mock(MinioClient.class);
        when(storage.getObject(any(GetObjectArgs.class))).thenReturn(new GetObjectResponse(
                new Headers.Builder().build(), "bucket", "region", "evidence_1.json",
                new ByteArrayInputStream(evidence.getBytes(StandardCharsets.UTF_8))));
        var saved = new java.util.concurrent.atomic.AtomicReference<JsonNode>();
        when(storage.putObject(any(PutObjectArgs.class))).thenAnswer(call -> {
            PutObjectArgs args = call.getArgument(0);
            saved.set(json.readTree(args.stream()));
            return null;
        });
        HttpClient http = mock(HttpClient.class);
        HttpResponse<String> limited = mock(HttpResponse.class);
        when(limited.statusCode()).thenReturn(200);
        when(limited.body()).thenReturn("{\"done_reason\":\"length\",\"message\":{\"content\":\"{\"}}");
        HttpResponse<String> first = summaryResponse(json, "Panel membincangkan isu jerebu.");
        HttpResponse<String> second = summaryResponse(json, "Kesihatan awam turut dibincangkan oleh panel.");
        HttpResponse<String> firstPerson = summaryResponse(json, "Saya sedang berfikir tentang kesihatan awam.");
        if (truncated) {
            when(http.send(any(HttpRequest.class), any(HttpResponse.BodyHandler.class)))
                    .thenReturn(limited, first, firstPerson, second);
        } else {
            when(http.send(any(HttpRequest.class), any(HttpResponse.BodyHandler.class)))
                    .thenReturn(first, second);
        }
        var activity = new RapporteurActivity(storage, "bucket", URI.create("http://localhost/api/chat"),
                "test", "python3", http);

        activity.section1(new ReportPipelineActivities.Files("bucket", "results/job/transcript.json",
                "results/job/kpn/Rapporteur_Kertas_01.docx", "work/", null));

        assertEquals(2, saved.get().path("paragraphs").size());
        assertEquals("Panel membincangkan isu jerebu.", saved.get().path("paragraphs").get(0).path("text").asText());
        assertEquals("Kesihatan awam turut dibincangkan oleh panel.",
                saved.get().path("paragraphs").get(1).path("text").asText());
        verify(http, times(truncated ? 4 : 2)).send(any(HttpRequest.class), any(HttpResponse.BodyHandler.class));
    }

    @SuppressWarnings("unchecked")
    private static HttpResponse<String> summaryResponse(ObjectMapper json, String text) throws Exception {
        HttpResponse<String> response = mock(HttpResponse.class);
        var content = json.createObjectNode();
        content.putArray("paragraphs").addObject().put("text", text).putArray("sourceIds").add(1);
        var reply = json.createObjectNode().put("done_reason", "stop");
        reply.putObject("message").put("content", content.toString());
        when(response.statusCode()).thenReturn(200);
        when(response.body()).thenReturn(reply.toString());
        return response;
    }

    @Test
    void discardsInventedExcerptsAndHeadingEchoes() {
        String heading = "3. Kandungan utama kertas pembentangan";
        String evidence = "Perlembagaan ialah asas kepada perpaduan.";
        String source = evidence + "\n" + heading;
        assertEquals(evidence, RapporteurActivity.verifiedExcerpt(source, evidence, heading));
        assertNull(RapporteurActivity.verifiedExcerpt(source, "Perlembagaan sudah dimansuhkan.", heading));
        assertNull(RapporteurActivity.verifiedExcerpt(source, heading, heading));
    }
}
