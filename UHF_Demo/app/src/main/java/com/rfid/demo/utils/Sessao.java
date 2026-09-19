package com.rfid.demo.utils;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Metadados de uma sessão de coleta do MVP (README, seção 5.2).
 * Sala, técnica e potência são fixados ao iniciar e não mudam durante a sessão.
 */
public class Sessao {
    public final String id;
    public final String sala;
    public final String tecnica;
    public final int potenciaDbm;
    public final long inicio;
    public volatile long fim = 0;

    public Sessao(String sala, String tecnica, int potenciaDbm, long inicio) {
        this.sala = sala;
        this.tecnica = tecnica;
        this.potenciaDbm = potenciaDbm;
        this.inicio = inicio;
        this.id = gerarId(sala, tecnica, inicio);
    }

    public boolean encerrada() {
        return fim > 0;
    }

    /** <sala>_<tecnica>_<yyyyMMdd-HHmmss>, apenas caracteres seguros para nome de arquivo. */
    private static String gerarId(String sala, String tecnica, long inicio) {
        String data = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date(inicio));
        return slug(sala) + "_" + slug(tecnica) + "_" + data;
    }

    private static String slug(String s) {
        return s.trim().replaceAll("[^A-Za-z0-9]+", "-");
    }
}
