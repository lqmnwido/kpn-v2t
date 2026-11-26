package com.example.kpn;

import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.stream.IntStream;

final class ReportTemplate {
    static final String FILE_NAME = "Rapporteur_Kertas_01.docx";
    static final List<String> HEADINGS = List.of(
            "Fokus utama kertas pembentangan yang dibentangkan",
            "Pernyataan masalah (problem statement)",
            "Kandungan utama kertas pembentangan",
            "Cabaran dalam pembinaan negara bangsa",
            "Hala tuju masa hadapan untuk pembinaan negara bangsa",
            "Cadangan yang diberikan untuk pembinaan negara bangsa",
            "Input sesi soal jawab dan interaksi bersama peserta",
            "Rumusan daripada kertas yang wajar diambil kira untuk pembinaan negara bangsa");
    static List<String> numberedHeadings() {
        return IntStream.range(0, HEADINGS.size()).mapToObj(i -> (i + 1) + ". " + HEADINGS.get(i)).toList();
    }

    record Paragraph(int number, String text) {}

    record Section(int number, String heading, String text, List<String> excerpts, List<Paragraph> paragraphs) {
        static Section create(int number, String heading, List<String> text, List<String> excerpts) {
            List<Paragraph> paragraphs = IntStream.range(0, text.size())
                    .mapToObj(i -> new Paragraph(i + 1, text.get(i))).toList();
            return new Section(number, heading, String.join("\n", text), excerpts, paragraphs);
        }
    }

    static byte[] render(byte[] reportJson, String pythonExecutable) throws Exception {
        return render(reportJson, null, pythonExecutable);
    }

    static byte[] render(byte[] reportJson, byte[] uploadedTemplate, String pythonExecutable) throws Exception {
        return runRenderer(reportJson, uploadedTemplate, pythonExecutable, false);
    }

    static void validate(byte[] uploadedTemplate, String pythonExecutable) throws Exception {
        byte[] headings = new ObjectMapper().writeValueAsBytes(numberedHeadings());
        runRenderer(headings, uploadedTemplate, pythonExecutable, true);
    }

    private static byte[] runRenderer(byte[] payload, byte[] uploadedTemplate, String pythonExecutable,
                                      boolean validateOnly) throws Exception {
        Path directory = Files.createTempDirectory("kpn-report-");
        Path script = directory.resolve("fill_report.py");
        Path template = directory.resolve("template.docx");
        Path input = directory.resolve("output.json");
        Path output = directory.resolve(FILE_NAME);
        Path log = directory.resolve("renderer.log");
        try {
            copyResource("/fill_report.py", script);
            if (uploadedTemplate == null) {
                copyResource("/" + FILE_NAME, template);
            } else {
                Files.write(template, uploadedTemplate);
            }
            Files.write(input, payload);
            List<String> command = validateOnly
                    ? List.of(pythonExecutable, script.toString(), "--validate", template.toString(), input.toString())
                    : List.of(pythonExecutable, script.toString(), template.toString(), input.toString(), output.toString());
            Process renderer = new ProcessBuilder(command).redirectErrorStream(true).redirectOutput(log.toFile()).start();
            try {
                if (!renderer.waitFor(60, TimeUnit.SECONDS)) {
                    throw new IOException("python-docx rendering timed out");
                }
                if (renderer.exitValue() != 0) {
                    throw new IOException("python-docx rendering failed: " + Files.readString(log).strip());
                }
            } finally {
                if (renderer.isAlive()) {
                    renderer.destroyForcibly();
                    renderer.waitFor(5, TimeUnit.SECONDS);
                }
            }
            return validateOnly ? null : Files.readAllBytes(output);
        } finally {
            for (Path path : List.of(log, output, input, template, script, directory)) {
                Files.deleteIfExists(path);
            }
        }
    }

    private static void copyResource(String name, Path destination) throws IOException {
        try (var resource = ReportTemplate.class.getResourceAsStream(name)) {
            if (resource == null) {
                throw new IOException("Missing report resource " + name);
            }
            Files.copy(resource, destination);
        }
    }
}
