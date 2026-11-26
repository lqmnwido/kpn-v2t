package com.example.kpn;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.minio.GetObjectArgs;
import io.minio.GetObjectResponse;
import io.minio.ListObjectsArgs;
import io.minio.MinioClient;
import io.minio.PutObjectArgs;
import io.temporal.api.enums.v1.EventType;
import io.temporal.client.WorkflowOptions;
import io.temporal.testing.TestWorkflowEnvironment;
import okhttp3.Headers;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;

import java.io.ByteArrayInputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;
import java.util.zip.ZipInputStream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class ReportWorkflowTest {
    private static final String PYTHON = System.getenv().getOrDefault("KPN_PYTHON_EXECUTABLE", "python3");

    @Test
    @Timeout(30)
    void schedulesEachStageAndWritesAllOutputs() throws Exception {
        String transcriptKey = "results/job/transcript.json";
        String outputKey = "results/job/kpn/Rapporteur_Kertas_01.docx";
        var objects = new ConcurrentHashMap<String, byte[]>();
        objects.put(transcriptKey, "{\"text\":\" \"}".getBytes(StandardCharsets.UTF_8));
        var sectionStarted = new CountDownLatch(1);
        var finishSection = new CountDownLatch(1);
        MinioClient storage = mock(MinioClient.class);
        when(storage.listObjects(any(ListObjectsArgs.class))).thenReturn(List.of());
        when(storage.getObject(any(GetObjectArgs.class))).thenAnswer(call -> {
            GetObjectArgs args = call.getArgument(0);
            return new GetObjectResponse(new Headers.Builder().build(), args.bucket(), null, args.object(),
                    new ByteArrayInputStream(objects.get(args.object())));
        });
        when(storage.putObject(any(PutObjectArgs.class))).thenAnswer(call -> {
            PutObjectArgs args = call.getArgument(0);
            if (args.object().endsWith("/section_1.json")) {
                sectionStarted.countDown();
                assertTrue(finishSection.await(10, TimeUnit.SECONDS));
            }
            objects.put(args.object(), args.stream().readAllBytes());
            return null;
        });

        try (var temporal = TestWorkflowEnvironment.newInstance()) {
            var worker = temporal.newWorker("KPN_OUTPUT_QUEUE");
            worker.registerWorkflowImplementationTypes(KpnOutputApplication.ReportWorkflowImpl.class);
            worker.registerActivitiesImplementations(new RapporteurActivity(storage, "bucket",
                    URI.create("http://localhost/api/chat"), "test", PYTHON));
            temporal.start();

            var client = temporal.getWorkflowClient();
            var report = client.newUntypedWorkflowStub("KpnRapporteurReport",
                    WorkflowOptions.newBuilder().setTaskQueue("KPN_OUTPUT_QUEUE").build());
            var execution = report.start("bucket", transcriptKey, outputKey);
            try {
                assertTrue(sectionStarted.await(10, TimeUnit.SECONDS));
                assertEquals(new KpnOutputApplication.ReportProgress("SECTION_1", 16),
                        report.query("kpnReportProgress", KpnOutputApplication.ReportProgress.class));
            } finally {
                finishSection.countDown();
            }

            assertEquals(outputKey, report.getResult(15, TimeUnit.SECONDS, String.class));
            assertEquals(new KpnOutputApplication.ReportProgress("DONE", 100),
                    report.query("kpnReportProgress", KpnOutputApplication.ReportProgress.class));
            var names = client.fetchHistory(execution.getWorkflowId(), execution.getRunId()).getEvents().stream()
                    .filter(event -> event.getEventType() == EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED)
                    .map(event -> event.getActivityTaskScheduledEventAttributes().getActivityType().getName())
                    .toList();
            assertEquals(List.of("ChunkTranscript", "ExtractEvidence", "Section_1", "Section_2", "Section_3",
                    "Section_4", "Section_5", "Section_6", "Section_7", "Section_8", "WriteReportJson", "RenderDocx"),
                    names);
            assertTrue(objects.containsKey("kpn/job/chunk_transcript/chunk_1.json"));
            assertTrue(objects.containsKey(outputKey));
            byte[] json = objects.get("results/job/kpn/kpn-report/output.json");
            assertEquals(8, new ObjectMapper().readTree(json).path("sections").size());
            String templateKey = "results/job/kpn/templates/example/Rapporteur_Kertas_03.docx";
            try (var bundled = ReportTemplate.class.getResourceAsStream("/" + ReportTemplate.FILE_NAME)) {
                objects.put(templateKey, bundled.readAllBytes());
            }
            String selectedOutput = "results/job/kpn/Rapporteur_Kertas_03.docx";
            var custom = client.newUntypedWorkflowStub("KpnRapporteurReport",
                    WorkflowOptions.newBuilder().setTaskQueue("KPN_OUTPUT_QUEUE").build());
            custom.start("bucket", transcriptKey, selectedOutput, templateKey);
            assertEquals(selectedOutput, custom.getResult(15, TimeUnit.SECONDS, String.class));
            assertTrue(objects.containsKey(selectedOutput));
            assertTrue(objects.keySet().stream().anyMatch(key -> key.endsWith("/template.docx")));
        }
    }

    @Test
    void rendererWritesNumberedParagraphsInAnswerCells() throws Exception {
        var sections = IntStream.rangeClosed(1, 8).mapToObj(i -> ReportTemplate.Section.create(i,
                ReportTemplate.numberedHeadings().get(i - 1),
                i == 1 ? List.of("Dapatan & catatan 1", "Penjelasan tambahan") : List.of("Dapatan & catatan " + i),
                List.of())).toList();
        byte[] json = new ObjectMapper().writeValueAsBytes(Map.of("sections", sections));
        byte[] docx = ReportTemplate.render(json, PYTHON);
        try (var zip = new ZipInputStream(new ByteArrayInputStream(docx))) {
            for (var entry = zip.getNextEntry(); entry != null; entry = zip.getNextEntry()) {
                if (entry.getName().equals("word/document.xml")) {
                    String xml = new String(zip.readAllBytes(), StandardCharsets.UTF_8);
                    int previous = -1;
                    for (int i = 1; i <= 8; i++) {
                        int heading = xml.indexOf(i + ". " + ReportTemplate.HEADINGS.get(i - 1), previous);
                        int section = xml.indexOf("1. Dapatan &amp; catatan " + i, heading);
                        assertTrue(heading > previous && section > heading);
                        int tableEnd = xml.indexOf("</w:tbl>", heading);
                        assertTrue(section < tableEnd);
                        int additional = xml.indexOf("2. Penjelasan tambahan", section);
                        if (i == 1) {
                            assertTrue(additional > xml.indexOf("</w:p>", section) && additional < tableEnd);
                        } else {
                            assertEquals(-1, additional);
                        }
                        previous = section;
                    }
                    assertTrue(xml.contains("YBhg. Prof. Emeritus Datuk Dr. Shad Saleem Faruqi"));
                    assertTrue(xml.contains("________________________________"));
                    return;
                }
            }
        }
        throw new IllegalStateException("Rendered DOCX did not contain document XML");
    }
}
