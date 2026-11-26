package com.example.asr.api.service;

import io.temporal.api.history.v1.HistoryEvent;

import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

final class KpnHistoryProgress {
    private static final Map<String, String> STAGES = Map.ofEntries(
            Map.entry("ChunkTranscript", "CHUNKING"),
            Map.entry("ExtractEvidence", "EXTRACTING_EVIDENCE"),
            Map.entry("Section_1", "SECTION_1"),
            Map.entry("Section_2", "SECTION_2"),
            Map.entry("Section_3", "SECTION_3"),
            Map.entry("Section_4", "SECTION_4"),
            Map.entry("Section_5", "SECTION_5"),
            Map.entry("Section_6", "SECTION_6"),
            Map.entry("Section_7", "SECTION_7"),
            Map.entry("Section_8", "SECTION_8"),
            Map.entry("WriteReportJson", "WRITING_JSON"),
            Map.entry("RenderDocx", "RENDERING_DOCX"));

    private KpnHistoryProgress() {}

    static TemporalJobService.ReportProgress from(List<HistoryEvent> events) {
        var pending = new LinkedHashMap<Long, String>();
        var completed = new HashSet<String>();
        boolean legacyCompleted = false;
        for (HistoryEvent event : events) {
            switch (event.getEventType()) {
                case EVENT_TYPE_ACTIVITY_TASK_SCHEDULED -> {
                    String name = event.getActivityTaskScheduledEventAttributes().getActivityType().getName();
                    if (STAGES.containsKey(name) || "Generate".equals(name)) pending.put(event.getEventId(), name);
                }
                case EVENT_TYPE_ACTIVITY_TASK_COMPLETED -> {
                    String name = pending.remove(event.getActivityTaskCompletedEventAttributes().getScheduledEventId());
                    if ("Generate".equals(name)) legacyCompleted = true;
                    else if (name != null) completed.add(name);
                }
                default -> {
                }
            }
        }
        if (legacyCompleted || completed.size() == STAGES.size()) {
            return new TemporalJobService.ReportProgress("DONE", 100);
        }
        String stage = pending.isEmpty() ? (completed.isEmpty() ? "QUEUED" : "GENERATING")
                : STAGES.getOrDefault(pending.lastEntry().getValue(), "GENERATING");
        return new TemporalJobService.ReportProgress(stage, completed.size() * 100 / STAGES.size());
    }
}
