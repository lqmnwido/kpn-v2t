package com.example.asr.api.service;

import io.temporal.api.common.v1.ActivityType;
import io.temporal.api.enums.v1.EventType;
import io.temporal.api.history.v1.ActivityTaskCompletedEventAttributes;
import io.temporal.api.history.v1.ActivityTaskScheduledEventAttributes;
import io.temporal.api.history.v1.HistoryEvent;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;

class KpnHistoryProgressTest {
    @Test
    void countsCompletedStagesAndReportsTheActiveStage() {
        var events = List.of(scheduled(3, "ChunkTranscript"), completed(3),
                scheduled(8, "ExtractEvidence"), completed(8), scheduled(13, "Section_1"));
        assertEquals(new TemporalJobService.ReportProgress("SECTION_1", 16), KpnHistoryProgress.from(events));
    }

    @Test
    void reportsQueuedAndLegacyProgressWithoutEstimatingElapsedTime() {
        assertEquals(new TemporalJobService.ReportProgress("QUEUED", 0), KpnHistoryProgress.from(List.of()));
        assertEquals(new TemporalJobService.ReportProgress("GENERATING", 0),
                KpnHistoryProgress.from(List.of(scheduled(3, "Generate"))));
        assertEquals(new TemporalJobService.ReportProgress("DONE", 100),
                KpnHistoryProgress.from(List.of(scheduled(3, "Generate"), completed(3))));
    }

    private static HistoryEvent scheduled(long id, String name) {
        return HistoryEvent.newBuilder().setEventId(id).setEventType(EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED)
                .setActivityTaskScheduledEventAttributes(ActivityTaskScheduledEventAttributes.newBuilder()
                        .setActivityType(ActivityType.newBuilder().setName(name))).build();
    }

    private static HistoryEvent completed(long scheduledId) {
        return HistoryEvent.newBuilder().setEventType(EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED)
                .setActivityTaskCompletedEventAttributes(ActivityTaskCompletedEventAttributes.newBuilder()
                        .setScheduledEventId(scheduledId)).build();
    }
}
