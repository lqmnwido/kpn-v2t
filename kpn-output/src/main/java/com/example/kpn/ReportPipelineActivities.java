package com.example.kpn;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface ReportPipelineActivities {
    record Files(String bucket, String transcriptKey, String outputKey, String workPrefix, String templateKey) {}

    record ChunkManifest(String prefix, int count) {}

    ChunkManifest chunkTranscript(Files files) throws Exception;

    void extractEvidence(Files files, ChunkManifest chunks) throws Exception;

    @ActivityMethod(name = "Section_1")
    void section1(Files files) throws Exception;

    @ActivityMethod(name = "Section_2")
    void section2(Files files) throws Exception;

    @ActivityMethod(name = "Section_3")
    void section3(Files files) throws Exception;

    @ActivityMethod(name = "Section_4")
    void section4(Files files) throws Exception;

    @ActivityMethod(name = "Section_5")
    void section5(Files files) throws Exception;

    @ActivityMethod(name = "Section_6")
    void section6(Files files) throws Exception;

    @ActivityMethod(name = "Section_7")
    void section7(Files files) throws Exception;

    @ActivityMethod(name = "Section_8")
    void section8(Files files) throws Exception;

    void writeReportJson(Files files) throws Exception;

    String renderDocx(Files files) throws Exception;
}
