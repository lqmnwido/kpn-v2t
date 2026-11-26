package com.example.asr.api.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Value("${KPN_UI_PORT:5173}")
    private int uiPort;

    @Override
    public void addCorsMappings(CorsRegistry registry) {
        registry.addMapping("/**")
                .allowedOrigins(
                        "http://localhost:" + uiPort,
                        "http://127.0.0.1:" + uiPort,
                        "http://localhost:2400",
                        "https://dev.d-reams.com",
                        "http://localhost:3400",
                        "http://localhost:8083",
                        "https://gerbang.lokal.my/dreams/api/vtt/v1",
                        "http://localhost:9080"
                        
                )
                .allowedMethods("GET", "POST", "DELETE", "OPTIONS");
    }
}
