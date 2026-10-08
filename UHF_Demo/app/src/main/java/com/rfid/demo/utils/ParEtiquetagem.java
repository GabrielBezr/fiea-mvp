package com.rfid.demo.utils;

/**
 * Vínculo entre a etiqueta colada num bem e o seu número de patrimônio (RF03).
 *
 * O EPC pode vir de duas origens: da leitura RFID ou do QR impresso na própria
 * etiqueta, que carrega o mesmo EPC. Registrar qual delas foi usada importa:
 * etiqueta que só pôde ser capturada pelo QR é etiqueta que não respondeu ao
 * rádio, e provavelmente também não vai responder na varredura — foi o que a POC
 * encontrou em 18 de 19 bens metálicos.
 */
public class ParEtiquetagem {

    public static final String ORIGEM_RFID = "rfid";
    public static final String ORIGEM_MANUAL = "manual";

    public final long timestamp;
    public final String epc;
    public final String patrimonio;
    public final boolean metalico;
    public final String origemEpc;

    public ParEtiquetagem(long timestamp, String epc, String patrimonio,
                          boolean metalico, String origemEpc) {
        this.timestamp = timestamp;
        this.epc = epc;
        this.patrimonio = patrimonio;
        this.metalico = metalico;
        this.origemEpc = origemEpc;
    }
}
