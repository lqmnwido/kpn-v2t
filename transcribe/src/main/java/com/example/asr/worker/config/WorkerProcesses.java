package com.example.asr.worker.config;

import org.springframework.core.env.ConfigurableEnvironment;
import org.springframework.core.env.EnumerablePropertySource;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class WorkerProcesses {
    private final ConfigurableEnvironment environment;

    public WorkerProcesses(ConfigurableEnvironment environment) {
        this.environment = environment;
    }

    public ProcessBuilder create(List<String> command) {
        ProcessBuilder process = new ProcessBuilder(command);
        // Spring configuration is separate from the operating system environment.
        // Forward dotenv keys only, using Spring's precedence for their values.
        for (var source : environment.getPropertySources()) {
            if (source instanceof EnumerablePropertySource<?> file && file.getName().contains(".env[.properties]")) {
                for (String key : file.getPropertyNames()) {
                    if (key.matches("[A-Za-z_][A-Za-z0-9_]*")) {
                        process.environment().put(key, environment.getRequiredProperty(key));
                    }
                }
            }
        }
        return process;
    }
}
