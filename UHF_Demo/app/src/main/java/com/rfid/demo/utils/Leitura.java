package com.rfid.demo.utils;

/**
 * Um evento de leitura bruto, exatamente como o SDK entregou em onInventoryTag.
 * Sala, técnica e potência vêm da Sessao no momento da exportação.
 */
public class Leitura {
    public final long timestamp;
    public final String epc;
    public final int rssi;

    public Leitura(long timestamp, String epc, int rssi) {
        this.timestamp = timestamp;
        this.epc = epc;
        this.rssi = rssi;
    }
}
