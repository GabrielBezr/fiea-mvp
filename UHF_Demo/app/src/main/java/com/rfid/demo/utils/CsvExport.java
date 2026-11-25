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
 * Exportador CSV do MVP. Gera um arquivo por varredura, ao encerrar cada sala
 * (procedimento 7.4), e um arquivo por sessão, ao encerrar a unidade (RF04/RF11).
 * Convive com FileImport.daochu(), que segue gerando o .xls original.
 */
public class CsvExport {
    private static final String TAG = "CsvExport";
    private static final String SEP = ",";
    private static final String EOL = "\n";
    private static final String FORMATO_DATA = "yyyy-MM-dd HH:mm:ss.SSS";

    /** Pasta de destino: a mesma já usada pelo exportador .xls. */
    public static String pastaDestino() {
        return FileImport.xlsFilePath;
    }

    /** Gera leituras_<id_sessao>_v<nn>.csv com as leituras brutas da varredura. */
    public static boolean exportarVarredura(Varredura v, List<Leitura> leituras) {
        try {
            File pasta = prepararPasta();
            if (pasta == null) {
                return false;
            }
            File arquivo = new File(pasta,
                    "leituras_" + v.sessao.id + "_" + v.sufixoArquivo() + ".csv");
            escreverLeituras(arquivo, v, leituras);
            return true;
        } catch (Exception ex) {
            Log.e(TAG, "Falha ao exportar a varredura", ex);
            return false;
        }
    }

    /**
     * Gera pendencias_<id_sessao>_v<nn>.csv com as resolucoes (QR, manual,
     * faltante) da varredura. Grava a lista inteira, sobrescrevendo a anterior:
     * as resolucoes acontecem depois do leituras_*.csv, e sem este arquivo o
     * caminho USB perdia tudo o que o Wi-Fi levava.
     */
    public static boolean exportarPendencias(Varredura v, List<SyncManager.PendenciaResolvida> resolvidas) {
        try {
            File pasta = prepararPasta();
            if (pasta == null) {
                return false;
            }
            File arquivo = new File(pasta,
                    "pendencias_" + v.sessao.id + "_" + v.sufixoArquivo() + ".csv");
            BufferedWriter w = abrir(arquivo);
            try {
                w.write("id_sessao,unidade,operador,id_varredura,sala,"
                        + "patrimonio,epc,metodo,timestamp" + EOL);
                String prefixo = campo(v.sessao.id) + SEP
                        + campo(v.sessao.unidade) + SEP
                        + campo(v.sessao.operador) + SEP
                        + campo(v.id) + SEP
                        + campo(v.sala) + SEP;
                for (SyncManager.PendenciaResolvida p : resolvidas) {
                    w.write(prefixo
                            + campo(p.patrimonio) + SEP
                            + campo(p.epc) + SEP
                            + campo(p.metodo) + SEP
                            + campo(p.timestamp) + EOL);
                }
            } finally {
                w.close();
            }
            return true;
        } catch (Exception ex) {
            Log.e(TAG, "Falha ao exportar as pendencias", ex);
            return false;
        }
    }

    /** Gera sessao_<id_sessao>.csv com a unidade, o operador e o tempo total. */
    public static boolean exportarSessao(Sessao s) {
        try {
            File pasta = prepararPasta();
            if (pasta == null) {
                return false;
            }
            File arquivo = new File(pasta, "sessao_" + s.id + ".csv");
            BufferedWriter w = abrir(arquivo);
            try {
                SimpleDateFormat fmt = new SimpleDateFormat(FORMATO_DATA, Locale.US);
                w.write("id_sessao,unidade,operador,inicio,fim,duracao_s,n_varreduras" + EOL);
                w.write(campo(s.id) + SEP
                        + campo(s.unidade) + SEP
                        + campo(s.operador) + SEP
                        + instante(fmt, s.inicio) + SEP
                        + instante(fmt, s.fim) + SEP
                        + duracaoSegundos(s.inicio, s.fim) + SEP
                        + s.totalVarreduras() + EOL);
            } finally {
                w.close();
            }
            return true;
        } catch (Exception ex) {
            Log.e(TAG, "Falha ao exportar a sessao", ex);
            return false;
        }
    }

    /** Nome do arquivo de etiquetagem de uma campanha, fixado ao abrir a tela. */
    public static File arquivoEtiquetagem(long inicio) {
        String carimbo = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date(inicio));
        return new File(pastaDestino(), "etiquetagem_" + carimbo + ".csv");
    }

    /**
     * Acrescenta um par ao arquivo de etiquetagem, criando o cabeçalho na
     * primeira vez.
     *
     * A gravação é imediata, par a par, de propósito: uma campanha de 1.200 bens
     * leva horas, e segurar tudo em memória até um botão de exportar significaria
     * perder o dia inteiro de trabalho se o aparelho desligar.
     */
    public static boolean anexarEtiquetagem(File arquivo, ParEtiquetagem par) {
        try {
            File pasta = prepararPasta();
            if (pasta == null) {
                return false;
            }
            boolean novo = !arquivo.exists() || arquivo.length() == 0;
            BufferedWriter w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(arquivo, true), StandardCharsets.UTF_8));
            try {
                if (novo) {
                    w.write("timestamp,epc,patrimonio,metalico,origem_epc" + EOL);
                }
                SimpleDateFormat fmt = new SimpleDateFormat(FORMATO_DATA, Locale.US);
                w.write(instante(fmt, par.timestamp) + SEP
                        + campo(par.epc) + SEP
                        + campo(par.patrimonio) + SEP
                        + (par.metalico ? "sim" : "nao") + SEP
                        + campo(par.origemEpc) + EOL);
            } finally {
                w.close();
            }
            return true;
        } catch (Exception ex) {
            Log.e(TAG, "Falha ao gravar o par de etiquetagem", ex);
            return false;
        }
    }

    /** Nome do arquivo de captura das tags de referência de uma campanha. */
    public static File arquivoTagsSala(long inicio) {
        String carimbo = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US).format(new Date(inicio));
        return new File(pastaDestino(), "tags_sala_" + carimbo + ".csv");
    }

    /**
     * Acrescenta uma tag de referência ao arquivo, criando o cabeçalho na
     * primeira vez.
     *
     * O cabeçalho é exatamente o que o comando `inventario salas` espera, de modo
     * que o arquivo gerado aqui entra no cadastro sem conversão nenhuma — é a
     * mesma planilha que o administrador digitaria, só que preenchida pelo
     * aparelho.
     */
    public static boolean anexarTagSala(File arquivo, TagSala tag) {
        try {
            File pasta = prepararPasta();
            if (pasta == null) {
                return false;
            }
            boolean novo = !arquivo.exists() || arquivo.length() == 0;
            BufferedWriter w = new BufferedWriter(new OutputStreamWriter(
                    new FileOutputStream(arquivo, true), StandardCharsets.UTF_8));
            try {
                if (novo) {
                    w.write("unidade,sala,posicao,epc" + EOL);
                }
                w.write(campo(tag.unidade) + SEP
                        + campo(tag.sala) + SEP
                        + campo(tag.posicao) + SEP
                        + campo(tag.epc) + EOL);
            } finally {
                w.close();
            }
            return true;
        } catch (Exception ex) {
            Log.e(TAG, "Falha ao gravar a tag de referência", ex);
            return false;
        }
    }

    private static void escreverLeituras(File arquivo, Varredura v, List<Leitura> leituras)
            throws IOException {
        // Uma instância só, reutilizada no laço: SimpleDateFormat não é thread-safe,
        // mas a exportação roda inteira na thread principal, sobre uma cópia da lista.
        SimpleDateFormat fmt = new SimpleDateFormat(FORMATO_DATA, Locale.US);
        BufferedWriter w = abrir(arquivo);
        try {
            w.write("id_sessao,unidade,operador,id_varredura,sala,tecnica,"
                    + "potencia_dbm,varredura_inicio,varredura_fim,varredura_duracao_s,"
                    + "timestamp,epc,rssi" + EOL);
            // Metadados da sessão e da varredura, constantes em todas as linhas.
            String prefixo = campo(v.sessao.id) + SEP
                    + campo(v.sessao.unidade) + SEP
                    + campo(v.sessao.operador) + SEP
                    + campo(v.id) + SEP
                    + campo(v.sala) + SEP
                    + campo(v.tecnica) + SEP
                    + v.potenciaDbm + SEP
                    + instante(fmt, v.inicio) + SEP
                    + instante(fmt, v.fim) + SEP
                    + duracaoSegundos(v.inicio, v.fim) + SEP;
            for (Leitura l : leituras) {
                w.write(prefixo);
                w.write(instante(fmt, l.timestamp));
                w.write(SEP);
                w.write(campo(l.epc));
                w.write(SEP);
                w.write(Integer.toString(l.rssi));
                w.write(EOL);
            }
        } finally {
            w.close();
        }
    }

    private static File prepararPasta() {
        File pasta = new File(pastaDestino());
        if (!pasta.exists() && !pasta.mkdirs()) {
            Log.e(TAG, "Nao foi possivel criar a pasta " + pasta);
            return null;
        }
        return pasta;
    }

    private static BufferedWriter abrir(File arquivo) throws IOException {
        return new BufferedWriter(
                new OutputStreamWriter(new FileOutputStream(arquivo, false), StandardCharsets.UTF_8));
    }

    /** Instante legível; vazio quando a marcação ainda não foi feita. */
    private static String instante(SimpleDateFormat fmt, long epochMs) {
        return epochMs > 0 ? fmt.format(new Date(epochMs)) : "";
    }

    /** Duração em segundos inteiros; vazia enquanto não houver fim registrado. */
    private static String duracaoSegundos(long inicio, long fim) {
        return fim > inicio ? Long.toString(Math.round((fim - inicio) / 1000.0)) : "";
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
