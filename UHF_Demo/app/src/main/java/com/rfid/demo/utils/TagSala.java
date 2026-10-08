package com.rfid.demo.utils;

/**
 * Tag de referência fixada numa posição da sala (RF01).
 *
 * São cinco por ambiente — quatro cantos e o centro — e é por elas que o
 * computador descobre a qual sala pertence cada varredura. Capturar o EPC aqui
 * evita transcrever à mão 24 caracteres hexadecimais, cinco vezes por sala.
 */
public class TagSala {

    public final String unidade;
    public final String sala;
    public final String posicao;
    public final String epc;

    public TagSala(String unidade, String sala, String posicao, String epc) {
        this.unidade = unidade;
        this.sala = sala;
        this.posicao = posicao;
        this.epc = epc;
    }
}
