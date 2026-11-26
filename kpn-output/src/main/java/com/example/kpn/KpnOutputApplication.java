package com.example.kpn;

import io.minio.MinioClient;
import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityOptions;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowClientOptions;
import io.temporal.common.RetryOptions;
import io.temporal.serviceclient.WorkflowServiceStubs;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import io.temporal.worker.Worker;
import io.temporal.worker.WorkerFactory;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.Workflow;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.SpringApplication;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

import java.net.URI;
import java.time.Duration;
import java.util.List;

@Configuration(proxyBeanMethods = false)
public class KpnOutputApplication {
    public static void main(String[] args) {
        SpringApplication.run(KpnOutputApplication.class, args);
    }

    @Bean(destroyMethod = "shutdown")
    WorkflowServiceStubs temporalService(@Value("${spring.temporal.connection.target}") String target) {
        return WorkflowServiceStubs.newServiceStubs(
                WorkflowServiceStubsOptions.newBuilder().setTarget(target).build());
    }

    @Bean
    RapporteurActivity rapporteurActivity(
            @Value("${app.minio.url}") String endpoint,
            @Value("${app.minio.access-key}") String accessKey,
            @Value("${app.minio.secret-key}") String secretKey,
            @Value("${app.minio.bucket}") String defaultBucket,
            @Value("${app.ollama.host}") URI ollamaHost,
            @Value("${app.ollama.model}") String model,
            @Value("${app.report.python-executable}") String pythonExecutable) {
        if (accessKey.isBlank() || secretKey.isBlank()) {
            throw new IllegalArgumentException("Set MINIO_ACCESS_KEY and MINIO_SECRET_KEY before starting kpn-output");
        }
        if (model.isBlank()) {
            throw new IllegalArgumentException("Set KPN_OLLAMA_MODEL before starting kpn-output");
        }
        MinioClient storage = MinioClient.builder()
                .endpoint(endpoint)
                .credentials(accessKey, secretKey)
                .build();
        return new RapporteurActivity(storage, defaultBucket, ollamaHost.resolve("/api/chat"), model, pythonExecutable);
    }

    @Bean(initMethod = "start", destroyMethod = "shutdown")
    WorkerFactory workerFactory(WorkflowServiceStubs service, RapporteurActivity report,
                                @Value("${spring.temporal.namespace}") String namespace) {
        WorkerFactory factory = WorkerFactory.newInstance(WorkflowClient.newInstance(service,
                WorkflowClientOptions.newBuilder().setNamespace(namespace).build()));
        Worker worker = factory.newWorker("KPN_OUTPUT_QUEUE");
        worker.registerWorkflowImplementationTypes(ReportWorkflowImpl.class);
        worker.registerActivitiesImplementations(report);
        return factory;
    }

    @WorkflowInterface
    public interface ReportWorkflow {
        @WorkflowMethod(name = "KpnRapporteurReport")
        String generate(String bucket, String transcriptKey, String outputKey, String templateKey);

        @QueryMethod(name = "kpnReportProgress")
        ReportProgress getProgress();
    }

    public record ReportProgress(String currentStep, Integer progressPercent) {}

    @ActivityInterface
    public interface ReportActivity {
        String generate(String bucket, String transcriptKey, String outputKey) throws Exception;
    }

    public static class ReportWorkflowImpl implements ReportWorkflow {
        private static final RetryOptions RETRY = RetryOptions.newBuilder().setMaximumAttempts(3).build();
        private static final int TOTAL_STEPS = ReportTemplate.HEADINGS.size() + 4;

        private String currentStep = "QUEUED";
        private int completedSteps;

        @Override
        public ReportProgress getProgress() {
            Integer percent = "GENERATING".equals(currentStep) ? null : completedSteps * 100 / TOTAL_STEPS;
            return new ReportProgress(currentStep, percent);
        }

        @FunctionalInterface
        private interface SectionActivity {
            void run(ReportPipelineActivities.Files files) throws Exception;
        }

        @Override
        public String generate(String bucket, String transcriptKey, String outputKey, String templateKey) {
            try {
                if (Workflow.getVersion("kpn-report-stages", Workflow.DEFAULT_VERSION, 1) == Workflow.DEFAULT_VERSION) {
                    currentStep = "GENERATING";
                    String result = Workflow.newActivityStub(ReportActivity.class, activityOptions(Duration.ofHours(2)))
                            .generate(bucket, transcriptKey, outputKey);
                    completedSteps = TOTAL_STEPS;
                    currentStep = "DONE";
                    return result;
                }

                String outputPrefix = outputKey.substring(0, outputKey.lastIndexOf('/') + 1);
                var files = new ReportPipelineActivities.Files(bucket, transcriptKey, outputKey,
                        outputPrefix + "kpn-report/runs/" + Workflow.getInfo().getRunId() + "/", templateKey);
                var pipeline = Workflow.newActivityStub(ReportPipelineActivities.class, activityOptions(Duration.ofMinutes(10)));
                var analysis = Workflow.newActivityStub(ReportPipelineActivities.class, activityOptions(Duration.ofHours(2)));

                currentStep = "CHUNKING";
                var chunks = pipeline.chunkTranscript(files);
                completedSteps++;
                currentStep = "EXTRACTING_EVIDENCE";
                analysis.extractEvidence(files, chunks);
                completedSteps++;

                List<SectionActivity> sections = List.of(analysis::section1, analysis::section2, analysis::section3,
                        analysis::section4, analysis::section5, analysis::section6, analysis::section7, analysis::section8);
                for (int index = 0; index < sections.size(); index++) {
                    currentStep = "SECTION_" + (index + 1);
                    sections.get(index).run(files);
                    completedSteps++;
                }

                currentStep = "WRITING_JSON";
                pipeline.writeReportJson(files);
                completedSteps++;
                currentStep = "RENDERING_DOCX";
                String result = pipeline.renderDocx(files);
                completedSteps++;
                currentStep = "DONE";
                return result;
            } catch (Exception e) {
                throw Workflow.wrap(e);
            }
        }

        private static ActivityOptions activityOptions(Duration timeout) {
            return ActivityOptions.newBuilder().setStartToCloseTimeout(timeout).setRetryOptions(RETRY).build();
        }
    }
}
