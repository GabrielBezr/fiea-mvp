package com.rfid.demo.utils;

/**
 * Varredura de uma sala dentro de uma sessão de inventário.
 *
 * A sala não é digitada: ela é identificada pela tag de referência fixada na
 * entrada (RF01/RF05). O aplicativo grava o EPC dessa tag; a tradução para o
 * nome da sala é feita no computador, a partir do cadastro.
 */
public class Varredura {

    public final Sessao sessao;
    public final int numero;

    /** Identificador da varredura: <id_sessao>-v<nn>. */
    public final String id;

    public final String tecnica;
    public final int potenciaDbm;
    public final long inicio;
    public volatile long fim = 0;

    /** EPC da tag de referência da sala; vazio enquanto não for capturada. */
    public volatile String epcReferencia = "";

    public Varredura(Sessao sessao, int numero, String tecnica, int potenciaDbm, long inicio) {
        this.sessao = sessao;
        this.numero = numero;
        this.tecnica = tecnica;
        this.potenciaDbm = potenciaDbm;
        this.inicio = inicio;
        this.id = sessao.id + "-v" + String.format("%02d", numero);
    }

    /** Sufixo usado no nome do arquivo: v01, v02... */
    public String sufixoArquivo() {
        return String.format("v%02d", numero);
    }

    public boolean encerrada() {
        return fim > 0;
    }
}
