package com.rfid.demo.utils;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Sessão de inventário, uma por unidade (RF04). Agrupa as varreduras de todas as
 * salas percorridas e registra início e fim, base do tempo total exigido por RF11.
 */
public class Sessao {

    /** Número único da sessão (yyyyMMddHHmmssSSS do início). */
    public final String id;

    public final String unidade;
    public final String operador;
    public final long inicio;
    public volatile long fim = 0;

    private int varreduras = 0;

    public Sessao(String unidade, String operador, long inicio) {
        this.unidade = unidade;
        this.operador = operador;
        this.inicio = inicio;
        this.id = new SimpleDateFormat("yyyyMMddHHmmssSSS", Locale.US).format(new Date(inicio));
    }

    /** Formata os instantes da sessão para exibição e exportação legível. */
    public static String formatarInstante(long instante) {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
                .format(new Date(instante));
    }

    /** Numera a próxima varredura da sessão: 1, 2, 3... */
    public synchronized int proximoNumero() {
        return ++varreduras;
    }

    public synchronized int totalVarreduras() {
        return varreduras;
    }

    public boolean encerrada() {
        return fim > 0;
    }
}
