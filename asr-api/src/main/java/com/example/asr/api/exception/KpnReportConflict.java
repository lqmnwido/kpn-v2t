package com.example.asr.api.exception;

import org.springframework.http.HttpStatus;
import org.springframework.web.server.ResponseStatusException;

public final class KpnReportConflict extends ResponseStatusException {
    public KpnReportConflict(String code, String detail) {
        super(HttpStatus.CONFLICT, detail);
        getBody().setProperty("code", code);
    }
}
