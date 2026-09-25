package com.rfid.demo.utils;

import android.util.Log;

import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStreamWriter;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;

/**
 * Exportador CSV do MVP. Convive com FileImport.daochu(), que segue gerando o
 * .xls original. Grava na mesma pasta do .xls.
 */
public class CsvExport {
    private static final String TAG = "CsvExport";
    private static final String SEP = ",";
    private static final String EOL = "\n";
    private static final String FORMATO_TIMESTAMP = "yyyy-MM-dd HH:mm:ss.SSS";

    /** Pasta de destino: a mesma já usada pelo exportador .xls. */
    public static String pastaDestino() {
        return FileImport.xlsFilePath;
    }

    /** Gera leituras_<sala>_<tecnica>_<data>.csv. Retorna true se gravou. */
    public static boolean exportar(Sessao sessao, List<Leitura> leituras) {
        try {
            File pasta = new File(pastaDestino());
            if (!pasta.exists() && !pasta.mkdirs()) {
                Log.e(TAG, "Nao foi possivel criar a pasta " + pasta);
                return false;
            }
            escreverLeituras(new File(pasta, "leituras_" + sessao.nomeArquivo + ".csv"), sessao, leituras);
            return true;
        } catch (Exception ex) {
            Log.e(TAG, "Falha ao exportar CSV", ex);
            return false;
        }
    }

    private static void escreverLeituras(File arquivo, Sessao s, List<Leitura> leituras) throws IOException {
        // Uma instância só, reutilizada no laço: SimpleDateFormat não é thread-safe,
        // mas a exportação roda inteira na thread principal, sobre uma cópia da lista.
        SimpleDateFormat fmt = new SimpleDateFormat(FORMATO_TIMESTAMP, Locale.US);
        BufferedWriter w = abrir(arquivo);
        try {
            w.write("id_sessao,timestamp,epc,rssi,sala,tecnica,potencia_dbm" + EOL);
            // Constantes da sessão, repetidas em todas as linhas.
            String prefixo = campo(s.id) + SEP;
            String sufixo = SEP + campo(s.sala) + SEP + campo(s.tecnica) + SEP + s.potenciaDbm + EOL;
            for (Leitura l : leituras) {
                w.write(prefixo);
                w.write(fmt.format(new Date(l.timestamp)));
                w.write(SEP);
                w.write(campo(l.epc));
                w.write(SEP);
                w.write(Integer.toString(l.rssi));
                w.write(sufixo);
            }
        } finally {
            w.close();
        }
    }

    private static BufferedWriter abrir(File arquivo) throws IOException {
        return new BufferedWriter(new OutputStreamWriter(new FileOutputStream(arquivo, false), StandardCharsets.UTF_8));
    }

    /** Escapa um campo conforme RFC 4180 caso contenha vírgula, aspas ou quebra de linha. */
    private static String campo(String v) {
        if (v == null) return "";
        if (v.indexOf(',') < 0 && v.indexOf('"') < 0 && v.indexOf('\n') < 0 && v.indexOf('\r') < 0) {
            return v;
        }
        return "\"" + v.replace("\"", "\"\"") + "\"";
    }
}
