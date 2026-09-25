package com.rfid.demo.utils;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Metadados de uma sessão de coleta do MVP.
 * Sala, técnica e potência são fixados ao iniciar e não mudam durante a sessão.
 */
public class Sessao {

    /** Número único da sessão (yyyyMMddHHmmss do início), gravado em toda linha do CSV. */
    public final String id;

    /** Base descritiva do nome do arquivo: <sala>_<tecnica>_<yyyyMMdd-HHmmss>. */
    public final String nomeArquivo;

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
        Date d = new Date(inicio);
        this.id = new SimpleDateFormat("yyyyMMddHHmmss", Locale.US).format(d);
        this.nomeArquivo = slug(sala) + "_" + slug(tecnica) + "_"
                + new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(d);
    }

    public boolean encerrada() {
        return fim > 0;
    }

    /** Mantém apenas caracteres seguros para nome de arquivo. */
    private static String slug(String s) {
        return s.trim().replaceAll("[^A-Za-z0-9]+", "-");
    }
}
