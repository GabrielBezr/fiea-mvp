package com.rfid.demo;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.os.Bundle;
import android.util.Log;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import com.google.android.material.progressindicator.LinearProgressIndicator;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;
import com.rfid.demo.utils.Dialogos;
import com.rfid.demo.utils.Leitura;
import com.rfid.demo.utils.Sessao;
import com.rfid.demo.utils.SyncManager;
import com.rfid.demo.utils.Varredura;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Locale;
import java.util.Set;

/**
 * Tela de Conferência e Resolução de Pendências (RF09 e RF10).
 *
 * Dois níveis: a lista das salas que ficaram com pendências (cada varredura
 * encerrada no Inventário com algum bem não lido) e, ao tocar numa delas, os bens
 * da sala, que o operador resolve por:
 * 1. Leitura de QR Code com a câmera traseira
 * 2. Conferência manual com digitação do tombo
 * 3. Marcação formal de Item Faltante / Desaparecido
 */
public class TagsSalaActivity extends Activity implements View.OnClickListener {

    private static final String TAG = "TagsSalaActivity";
    private static final String ARQUIVO_CONFERENCIAS = "conferencias_pendentes.json";
    public static class ConferenciaPendente {
        public final Sessao sessao;
        public final Varredura varredura;
        public final List<Leitura> leituras;
        public final Set<String> epcsLidos;
        public final String sala;
        public final List<SyncManager.PendenciaResolvida> resolucoes = new ArrayList<>();

        public ConferenciaPendente(Sessao sessao, Varredura varredura, List<Leitura> leituras, Set<String> epcsLidos, String sala) {
            this.sessao = sessao;
            this.varredura = varredura;
            this.leituras = leituras != null ? new ArrayList<>(leituras) : new ArrayList<Leitura>();
            this.epcsLidos = epcsLidos != null ? new HashSet<>(epcsLidos) : new HashSet<String>();
            this.sala = sala != null ? sala : "";
        }
    }

    // Fila de varreduras pendentes de conferência (por sala)
    public static final Map<String, ConferenciaPendente> conferenciasPendentes = new LinkedHashMap<>();
    public static String salaAtiva = "";

    // Campos legados mantidos para compatibilidade direta
    public static Sessao sessaoVarredura = null;
    public static Varredura varreduraConcluida = null;
    public static List<Leitura> leiturasVarredura = new ArrayList<>();
    public static Set<String> epcsLidosVarredura = new HashSet<>();
    /**
     * Uma sala varrida com bens pendentes. Guarda a varredura inteira, porque as
     */
    static class SalaConferida {
        final Sessao sessao;
        final Varredura varredura;
        final List<Leitura> leituras;
        final Set<String> epcsLidos;
        final List<SyncManager.PendenciaResolvida> resolvidas = new ArrayList<>();
        boolean resolucoesNaoEnviadas = false;
        int total;
        int pendentes;

        SalaConferida(Sessao sessao, Varredura varredura, List<Leitura> leituras, Set<String> epcsLidos,
                      int total, int pendentes) {
            this.sessao = sessao;
            this.varredura = varredura;
            this.leituras = leituras;
            this.epcsLidos = epcsLidos;
            this.total = total;
            this.pendentes = pendentes;
        }
    }

    // Salas com pendências, a mais recente primeiro. Ficam na memória enquanto o app
    // estiver aberto; o que já foi resolvido vai ao PC (ou à fila) ao sair da sala.
    private static final List<SalaConferida> salasConferidas = new ArrayList<>();
    // "Resolver agora" no Inventário: ao entrar na aba, abre direto esta sala.
    private static SalaConferida salaParaAbrir = null;

    /**
     * Registra a sala recém-varrida (ScanActivity). Só entra na lista se ficou algum
     * bem pendente, que é o que há para resolver aqui.
     */
    public static void carregarParaConferencia(Sessao s, Varredura v, List<Leitura> leituras, Set<String> epcs,
                                               int total, int pendentes) {
        if (v == null || pendentes <= 0) {
            return;
        }
        for (int i = 0; i < salasConferidas.size(); i++) {
            if (salasConferidas.get(i).varredura.id.equals(v.id)) {
                salasConferidas.remove(i);
                break;
            }
        }
        salasConferidas.add(0, new SalaConferida(s, v,
                leituras != null ? new ArrayList<>(leituras) : new ArrayList<Leitura>(),
                epcs != null ? new HashSet<>(epcs) : new HashSet<String>(), total, pendentes));
    }

    /** "Resolver agora": a próxima vez que a aba aparecer, ela abre nesta varredura. */
    public static void abrirAoEntrar(Varredura v) {
        for (SalaConferida sala : salasConferidas) {
            if (sala.varredura == v) {
                salaParaAbrir = sala;
                return;
            }
        }
    }

    /** Quantos bens desta varredura ainda faltam resolver aqui; 0 se ela não tem pendências. */
    public static int pendentesDe(Varredura v) {
        for (SalaConferida sala : salasConferidas) {
            if (sala.varredura == v) {
                return sala.pendentes;
            }
        }
        return 0;
    }

    // Métodos de resolução (RF10), um botão para cada
    private static final int ACAO_MANUAL = 1;
    private static final int ACAO_FALTANTE = 2;

    private TextView tvPendSala, tvPendStatus, tvItemSelecionado, tvSyncStatus;
    private Spinner spinnerSalasPendentes;
    private TextView tvContRfid, tvContQr, tvContManual, tvContFaltante, tvContPendente;
    private LinearProgressIndicator pbPendencias;
    private View grupoSemVarredura, grupoSalas, grupoConferencia, btVoltarSalas;
    private Button btResolverQr, btResolverManual, btResolverFaltante;
    private Button btnSincronizarServidor, btIrInventario;
    private ListView lvPendencias, lvSalas;

    private final List<SyncManager.BemEsperado> listaBens = new ArrayList<>();
    private SyncManager.BemEsperado bemSelecionado = null;
    // Sala aberta; null mostra a lista de salas.
    private SalaConferida salaAberta = null;
    private BensAdapter adaptador;
    private SalasAdapter adaptadorSalas;
    private SyncManager syncManager;

    public static void carregarParaConferencia(Sessao s, Varredura v, List<Leitura> leituras, Set<String> epcs, String sala) {
        salvarParaConferencia(null, s, v, leituras, epcs, sala);
    }

    public static synchronized void salvarParaConferencia(Context ctx, Sessao s, Varredura v, List<Leitura> leituras, Set<String> epcs, String sala) {
        if (sala == null || sala.isEmpty()) return;
        ConferenciaPendente cp = new ConferenciaPendente(s, v, leituras, epcs, sala);
        conferenciasPendentes.put(sala, cp);
        salaAtiva = sala;

        // Atualiza campos estáticos legados
        sessaoVarredura = s;
        varreduraConcluida = v;
        leiturasVarredura = cp.leituras;
        epcsLidosVarredura = cp.epcsLidos;

        if (ctx != null) {
            salvarConferenciasNoDisco(ctx);
        }
    }

    public static synchronized void salvarConferenciasNoDisco(Context ctx) {
        if (ctx == null) return;
        try {
            JSONArray arr = new JSONArray();
            for (ConferenciaPendente cp : conferenciasPendentes.values()) {
                JSONObject obj = new JSONObject();
                obj.put("sala", cp.sala);

                if (cp.sessao != null) {
                    JSONObject sObj = new JSONObject();
                    sObj.put("id", cp.sessao.id);
                    sObj.put("unidade", cp.sessao.unidade);
                    sObj.put("operador", cp.sessao.operador);
                    sObj.put("inicio", cp.sessao.inicio);
                    sObj.put("fim", cp.sessao.fim);
                    obj.put("sessao", sObj);
                }

                if (cp.varredura != null) {
                    JSONObject vObj = new JSONObject();
                    vObj.put("id", cp.varredura.id);
                    vObj.put("numero", cp.varredura.numero);
                    vObj.put("sala", cp.varredura.sala);
                    vObj.put("tecnica", cp.varredura.tecnica);
                    vObj.put("potenciaDbm", cp.varredura.potenciaDbm);
                    vObj.put("inicio", cp.varredura.inicio);
                    vObj.put("fim", cp.varredura.fim);
                    vObj.put("epcReferencia", cp.varredura.epcReferencia != null ? cp.varredura.epcReferencia : "");
                    obj.put("varredura", vObj);
                }

                JSONArray lArr = new JSONArray();
                for (Leitura l : cp.leituras) {
                    JSONObject lo = new JSONObject();
                    lo.put("timestamp", l.timestamp);
                    lo.put("epc", l.epc);
                    lo.put("rssi", l.rssi);
                    lArr.put(lo);
                }
                obj.put("leituras", lArr);

                JSONArray eArr = new JSONArray();
                for (String epc : cp.epcsLidos) {
                    eArr.put(epc);
                }
                obj.put("epcsLidos", eArr);

                JSONArray rArr = new JSONArray();
                for (SyncManager.PendenciaResolvida pr : cp.resolucoes) {
                    JSONObject ro = new JSONObject();
                    ro.put("patrimonio", pr.patrimonio);
                    ro.put("epc", pr.epc);
                    ro.put("metodo", pr.metodo);
                    ro.put("timestamp", pr.timestamp);
                    rArr.put(ro);
                }
                obj.put("resolucoes", rArr);

                arr.put(obj);
            }

            File file = new File(ctx.getFilesDir(), ARQUIVO_CONFERENCIAS);
            FileOutputStream fos = new FileOutputStream(file, false);
            fos.write(arr.toString().getBytes(StandardCharsets.UTF_8));
            fos.close();
        } catch (Exception e) {
            Log.e(TAG, "Erro ao salvar conferências no disco: " + e.getMessage());
        }
    }

    public static synchronized void carregarConferenciasDoDisco(Context ctx) {
        if (ctx == null) return;
        try {
            File file = new File(ctx.getFilesDir(), ARQUIVO_CONFERENCIAS);
            if (!file.exists()) return;

            FileInputStream fis = new FileInputStream(file);
            byte[] data = new byte[(int) file.length()];
            fis.read(data);
            fis.close();

            String content = new String(data, StandardCharsets.UTF_8);
            JSONArray arr = new JSONArray(content);

            for (int i = 0; i < arr.length(); i++) {
                JSONObject obj = arr.getJSONObject(i);
                String sala = obj.optString("sala", "");
                if (sala.isEmpty()) continue;

                Sessao s = null;
                JSONObject sObj = obj.optJSONObject("sessao");
                if (sObj != null) {
                    s = new Sessao(
                            sObj.optString("unidade", "UFAL"),
                            sObj.optString("operador", "Operador"),
                            sObj.optLong("inicio", System.currentTimeMillis())
                    );
                    s.fim = sObj.optLong("fim", 0);
                }

                Varredura v = null;
                JSONObject vObj = obj.optJSONObject("varredura");
                if (vObj != null && s != null) {
                    v = new Varredura(
                            s,
                            vObj.optInt("numero", 1),
                            vObj.optString("sala", sala),
                            vObj.optString("tecnica", "estatica_porta"),
                            vObj.optInt("potenciaDbm", 30),
                            vObj.optLong("inicio", System.currentTimeMillis())
                    );
                    v.fim = vObj.optLong("fim", 0);
                    v.epcReferencia = vObj.optString("epcReferencia", "");
                }

                List<Leitura> leituras = new ArrayList<>();
                JSONArray lArr = obj.optJSONArray("leituras");
                if (lArr != null) {
                    for (int j = 0; j < lArr.length(); j++) {
                        JSONObject lo = lArr.getJSONObject(j);
                        leituras.add(new Leitura(
                                lo.optLong("timestamp", System.currentTimeMillis()),
                                lo.optString("epc", ""),
                                lo.optInt("rssi", 0)
                        ));
                    }
                }

                Set<String> epcs = new HashSet<>();
                JSONArray eArr = obj.optJSONArray("epcsLidos");
                if (eArr != null) {
                    for (int j = 0; j < eArr.length(); j++) {
                        epcs.add(eArr.getString(j));
                    }
                }

                ConferenciaPendente cp = new ConferenciaPendente(s, v, leituras, epcs, sala);

                JSONArray rArr = obj.optJSONArray("resolucoes");
                if (rArr != null) {
                    for (int j = 0; j < rArr.length(); j++) {
                        JSONObject ro = rArr.getJSONObject(j);
                        cp.resolucoes.add(new SyncManager.PendenciaResolvida(
                                ro.optString("patrimonio", ""),
                                ro.optString("epc", ""),
                                ro.optString("metodo", "MANUAL")
                        ));
                    }
                }

                if (!conferenciasPendentes.containsKey(sala)) {
                    conferenciasPendentes.put(sala, cp);
                }
            }

            if ((salaAtiva == null || salaAtiva.isEmpty()) && !conferenciasPendentes.isEmpty()) {
                salaAtiva = conferenciasPendentes.keySet().iterator().next();
            }
        } catch (Exception e) {
            Log.e(TAG, "Erro ao carregar conferências do disco: " + e.getMessage());
        }
    }

    public static synchronized void sincronizarTodasPendentes(Context ctx) {
        if (ctx == null) return;
        carregarConferenciasDoDisco(ctx);
        if (conferenciasPendentes.isEmpty()) return;

        SyncManager sm = SyncManager.getInstance(ctx);
        for (ConferenciaPendente cp : new ArrayList<>(conferenciasPendentes.values())) {
            sm.sincronizarVarredura(cp.sessao, cp.varredura, cp.leituras, cp.resolucoes, null);
        }
        conferenciasPendentes.clear();
        salaAtiva = "";
        salvarConferenciasNoDisco(ctx);
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_tags_sala);

        syncManager = SyncManager.getInstance(this);

        tvPendSala = findViewById(R.id.tv_pend_sala);
        spinnerSalasPendentes = findViewById(R.id.spinner_salas_pendentes);
        tvPendStatus = findViewById(R.id.tv_pend_status);
        tvItemSelecionado = findViewById(R.id.tv_item_selecionado);
        tvSyncStatus = findViewById(R.id.tv_sync_status);
        tvContRfid = findViewById(R.id.tv_cont_rfid);
        tvContQr = findViewById(R.id.tv_cont_qr);
        tvContManual = findViewById(R.id.tv_cont_manual);
        tvContFaltante = findViewById(R.id.tv_cont_faltante);
        tvContPendente = findViewById(R.id.tv_cont_pendente);
        pbPendencias = findViewById(R.id.pb_pendencias);
        grupoSemVarredura = findViewById(R.id.grupo_sem_varredura);
        grupoSalas = findViewById(R.id.grupo_salas);
        grupoConferencia = findViewById(R.id.grupo_conferencia);

        btResolverQr = findViewById(R.id.bt_resolver_qr);
        btResolverManual = findViewById(R.id.bt_resolver_manual);
        btResolverFaltante = findViewById(R.id.bt_resolver_faltante);
        btVoltarSalas = findViewById(R.id.bt_voltar_salas);
        btnSincronizarServidor = findViewById(R.id.btn_sincronizar_servidor);
        btIrInventario = findViewById(R.id.bt_ir_inventario);
        lvPendencias = findViewById(R.id.lv_pendencias);
        lvSalas = findViewById(R.id.lv_salas);

        btResolverQr.setOnClickListener(this);
        btResolverManual.setOnClickListener(this);
        btResolverFaltante.setOnClickListener(this);
        btVoltarSalas.setOnClickListener(this);
        btnSincronizarServidor.setOnClickListener(this);
        btIrInventario.setOnClickListener(this);

        adaptador = new BensAdapter(listaBens);
        lvPendencias.setAdapter(adaptador);
        adaptadorSalas = new SalasAdapter(salasConferidas);
        lvSalas.setAdapter(adaptadorSalas);

        lvPendencias.setOnItemClickListener((parent, view, position, id) -> {
            if (position < listaBens.size()) {
                bemSelecionado = listaBens.get(position);
                mostrarSelecionado();
                adaptador.notifyDataSetChanged();
            }
        });
        lvSalas.setOnItemClickListener((parent, view, position, id) -> {
            if (position < salasConferidas.size()) {
                abrirSala(salasConferidas.get(position));
            }
        });
        mostrarSelecionado();

        carregarConferenciasDoDisco(this);
    }

    private void mostrarSelecionado() {
        boolean selecionado = bemSelecionado != null;
        if (selecionado) {
            tvItemSelecionado.setText(getString(R.string.itemSelecionado, bemSelecionado.patrimonio,
                    bemSelecionado.descricao, formatarStatus(bemSelecionado.status)));
        }
        btResolverQr.setEnabled(selecionado);
        btResolverManual.setEnabled(selecionado);
        btResolverFaltante.setEnabled(selecionado);
    }

    /**
     * Solta a seleção e mostra a lista de bens do começo, com os pendentes em cima. Depois
     * de uma resolução a mensagem de confirmação fica no cartão (limparMensagem = false).
     */
    private void soltarSelecao(boolean limparMensagem) {
        bemSelecionado = null;
        if (limparMensagem) {
            tvItemSelecionado.setText(R.string.itemNenhum);
        }
        mostrarSelecionado();
        ordenarPendentesPrimeiro();
        adaptador.notifyDataSetChanged();
        lvPendencias.setSelection(0);
    }

    private void abrirSala(SalaConferida sala) {
        salaAberta = sala;
        listaBens.clear();
        adaptador.notifyDataSetChanged();
        soltarSelecao(true);
        atualizarTela();
    }

    /**
     * Botão "←" da sala e tecla Voltar (MainActivity): volta à lista de salas, mandando
     * antes o que foi resolvido na sala. Devolve false se já estava na lista.
     */
    public boolean voltarParaSalas() {
        if (salaAberta == null) {
            return false;
        }
        enviarResolucoes(salaAberta);
        salaAberta = null;
        soltarSelecao(true);
        atualizarTela();
        return true;
    }

    /** Resoluções que ainda não saíram do aparelho vão ao PC, ou à fila se estiver offline. */
    private void enviarResolucoes(SalaConferida sala) {
        if (sala == null || !sala.resolucoesNaoEnviadas) {
            return;
        }
        sala.resolucoesNaoEnviadas = false;
        syncManager.sincronizarVarredura(sala.sessao, sala.varredura, sala.leituras,
                new ArrayList<>(sala.resolvidas), null);
    }

    // Pendentes primeiro: é o que o operador ainda precisa resolver. A ordenação é
    // estável, então dentro de cada grupo vale a ordem que veio do PC.
    private void ordenarPendentesPrimeiro() {
        Collections.sort(listaBens, (a, b) -> Boolean.compare(!estaPendente(a), !estaPendente(b)));
    }

    @Override
    protected void onResume() {
        super.onResume();
        carregarConferenciasDoDisco(this);
        if (salaParaAbrir != null) {
            SalaConferida sala = salaParaAbrir;
            salaParaAbrir = null;
            if (sala != salaAberta) {
                enviarResolucoes(salaAberta);
                abrirSala(sala);
                verificarConexaoServidor();
                return;
            }
        }
        atualizarTela();
        verificarConexaoServidor();
    }

    private ConferenciaPendente getConferenciaAtiva() {
        if (salaAtiva != null && conferenciasPendentes.containsKey(salaAtiva)) {
            return conferenciasPendentes.get(salaAtiva);
        }
        if (!conferenciasPendentes.isEmpty()) {
            salaAtiva = conferenciasPendentes.keySet().iterator().next();
            return conferenciasPendentes.get(salaAtiva);
        }
        return null;
    }

    private void atualizarTela() {
        if (conferenciasPendentes.isEmpty()) {
            grupoSemVarredura.setVisibility(View.VISIBLE);
            grupoConferencia.setVisibility(View.GONE);
            return;
        }

        grupoSemVarredura.setVisibility(View.GONE);
        grupoConferencia.setVisibility(View.VISIBLE);

        if (salaAtiva == null || salaAtiva.isEmpty() || !conferenciasPendentes.containsKey(salaAtiva)) {
            salaAtiva = conferenciasPendentes.keySet().iterator().next();
        }

        final ConferenciaPendente cp = getConferenciaAtiva();
        if (cp == null) {
            grupoSemVarredura.setVisibility(View.VISIBLE);
            grupoConferencia.setVisibility(View.GONE);
            return;
        }

        // Atualiza campos estáticos legados com a conferência ativa
        sessaoVarredura = cp.sessao;
        varreduraConcluida = cp.varredura;
        leiturasVarredura = cp.leituras;
        epcsLidosVarredura = cp.epcsLidos;

        // Se houver mais de uma sala com pendência, exibe o seletor (Spinner)
        if (conferenciasPendentes.size() > 1 && spinnerSalasPendentes != null) {
            tvPendSala.setVisibility(View.GONE);
            spinnerSalasPendentes.setVisibility(View.VISIBLE);

            List<String> salas = new ArrayList<>(conferenciasPendentes.keySet());
            ArrayAdapter<String> adapter = new ArrayAdapter<>(this, R.layout.item_spinner, salas);
            adapter.setDropDownViewResource(R.layout.item_spinner_dropdown);
            spinnerSalasPendentes.setAdapter(adapter);

            int pos = salas.indexOf(salaAtiva);
            if (pos >= 0) {
                spinnerSalasPendentes.setSelection(pos);
            }

            spinnerSalasPendentes.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
                @Override
                public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                    String escolhida = salas.get(position);
                    if (!escolhida.equals(salaAtiva)) {
                        salaAtiva = escolhida;
                        atualizarTela();
                    }
                }

                @Override
                public void onNothingSelected(AdapterView<?> parent) {}
            });
        } else {
            if (spinnerSalasPendentes != null) {
                spinnerSalasPendentes.setVisibility(View.GONE);
            }
            tvPendSala.setVisibility(View.VISIBLE);
            tvPendSala.setText(getString(R.string.pendSala, salaAtiva));
        }

        carregarBensDaSalaAtiva();
    }

    private void carregarBensDaSalaAtiva() {
        final ConferenciaPendente cp = getConferenciaAtiva();
        if (cp == null) return;

        final String sala = cp.sala;
        syncManager.buscarBensDaSala(sala, bens -> {
            if (!sala.equals(salaAtiva)) return;

            listaBens.clear();
            if (bens != null) {
                for (SyncManager.BemEsperado b : bens) {
                    if (b.epc != null && !b.epc.isEmpty() && cp.epcsLidos.contains(b.epc.toUpperCase())) {
                        b.status = "LOCALIZADO_RFID";
                    }
                    for (SyncManager.PendenciaResolvida pr : cp.resolucoes) {
    @Override
    protected void onPause() {
        super.onPause();
        // Trocar de aba ou sair do app também leva ao PC o que foi resolvido.
        for (SalaConferida sala : salasConferidas) {
            enviarResolucoes(sala);
        }
    }

    private void atualizarTela() {
        // Sem salas pendentes, a tela inteira vira a explicação de como chegar aqui.
        boolean semSalas = salasConferidas.isEmpty();
        grupoSemVarredura.setVisibility(semSalas ? View.VISIBLE : View.GONE);
        grupoSalas.setVisibility(!semSalas && salaAberta == null ? View.VISIBLE : View.GONE);
        grupoConferencia.setVisibility(!semSalas && salaAberta != null ? View.VISIBLE : View.GONE);
        if (semSalas) {
            return;
        }
        if (salaAberta == null) {
            adaptadorSalas.notifyDataSetChanged();
            return;
        }

        final SalaConferida sala = salaAberta;
        tvPendSala.setText(getString(R.string.pendSala, sala.varredura.sala));

        // Carrega bens da sala via SyncManager (rede ou cache offline)
        syncManager.buscarBensDaSala(sala.varredura.sala, sala.sessao != null ? sala.sessao.unidade : null, bens -> {
            if (sala != salaAberta) {
                return; // o operador já saiu desta sala enquanto a busca corria
            }
            listaBens.clear();
            if (bens != null) {
                for (SyncManager.BemEsperado b : bens) {
                    // Cruzamento RF09: Se o EPC foi lido via RFID na varredura, marca como LOCALIZADO_RFID
                    if (b.epc != null && !b.epc.isEmpty() && sala.epcsLidos.contains(b.epc.toUpperCase())) {
                        b.status = "LOCALIZADO_RFID";
                    }
                    // Mantém resoluções já feitas nesta sala
                    for (SyncManager.PendenciaResolvida pr : sala.resolvidas) {
                        if (pr.patrimonio.equalsIgnoreCase(b.patrimonio)) {
                            b.status = "LOCALIZADO_" + pr.metodo;
                            break;
                        }
                    }
                    listaBens.add(b);
                }
            }
            ordenarPendentesPrimeiro();
            // A lista é recriada a cada carga; a seleção passa para o objeto novo do
            // mesmo bem, senão as ações seguintes mexeriam num objeto fora da tela.
            if (bemSelecionado != null) {
                for (SyncManager.BemEsperado b : listaBens) {
                    if (b.patrimonio != null && b.patrimonio.equalsIgnoreCase(bemSelecionado.patrimonio)) {
                        bemSelecionado = b;
                        break;
                    }
                }
            }

            Collections.sort(listaBens, (a, b) -> Boolean.compare(!estaPendente(a), !estaPendente(b)));

            bemSelecionado = null;
            mostrarSelecionado();
            atualizarContadores();
            adaptador.notifyDataSetChanged();
        });
    }

    private void atualizarContadores() {
        int total = listaBens.size();
        int rfid = 0, qr = 0, manual = 0, faltante = 0, pendentes = 0;

        for (SyncManager.BemEsperado b : listaBens) {
            if ("LOCALIZADO_RFID".equals(b.status)) rfid++;
            else if ("LOCALIZADO_QR".equals(b.status)) qr++;
            else if ("LOCALIZADO_MANUAL".equals(b.status)) manual++;
            else if ("LOCALIZADO_FALTANTE".equals(b.status) || "FALTANTE".equals(b.status)) faltante++;
            else pendentes++;
        }

        tvPendStatus.setText(getString(R.string.pendProgresso, total - pendentes, total));
        pbPendencias.setMax(total);
        pbPendencias.setProgressCompat(total - pendentes, true);
        tvContRfid.setText(String.valueOf(rfid));
        tvContQr.setText(String.valueOf(qr));
        tvContManual.setText(String.valueOf(manual));
        tvContFaltante.setText(String.valueOf(faltante));
        tvContPendente.setText(String.valueOf(pendentes));
        // A lista de salas mostra quanto falta em cada uma.
        if (salaAberta != null) {
            salaAberta.total = total;
            salaAberta.pendentes = pendentes;
        }
    }

    private static boolean estaPendente(SyncManager.BemEsperado b) {
        return !"LOCALIZADO_RFID".equals(b.status) && !"LOCALIZADO_QR".equals(b.status)
                && !"LOCALIZADO_MANUAL".equals(b.status) && !"LOCALIZADO_FALTANTE".equals(b.status)
                && !"FALTANTE".equals(b.status);
    }

    private void verificarConexaoServidor() {
        tvSyncStatus.setText(R.string.syncVerificando);
        tvSyncStatus.setTextColor(ContextCompat.getColor(this, R.color.texto_secundario));
        syncManager.testarConexao(online -> {
            int naFila = syncManager.totalPendenciasNaFila();
            String texto = online
                    ? getString(R.string.syncOnline, syncManager.getServerUrl())
                    : getString(R.string.syncOffline);
            if (naFila > 0) {
                texto = getString(R.string.conexaoNaFila, texto, naFila);
            }
            tvSyncStatus.setText(texto);
            tvSyncStatus.setTextColor(ContextCompat.getColor(this, online ? R.color.sucesso_texto : R.color.erro_texto));
        });
    }

    @Override
    public void onClick(View v) {
        if (v == btResolverQr) {
            executarAcao(ACAO_QR);
        } else if (v == btResolverManual) {
            executarAcao(ACAO_MANUAL);
        } else if (v == btResolverFaltante) {
            executarAcao(ACAO_FALTANTE);
        } else if (v == btVoltarSalas) {
            voltarParaSalas();
        } else if (v == btnSincronizarServidor) {
            sincronizarComServidor();
        } else if (v == btIrInventario && MainActivity.myTabHost != null) {
            MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_INVENTARIO);
        }
    }

    private void adicionarResolucao(SyncManager.PendenciaResolvida nova) {
        ConferenciaPendente cp = getConferenciaAtiva();
        if (cp != null) {
            for (int i = 0; i < cp.resolucoes.size(); i++) {
                if (cp.resolucoes.get(i).patrimonio.equalsIgnoreCase(nova.patrimonio)) {
                    cp.resolucoes.remove(i);
                    break;
                }
            }
            cp.resolucoes.add(nova);
            salvarConferenciasNoDisco(this);
        }
    }

    private void executarAcao(int opcao) {
        if (bemSelecionado == null || salaAberta == null) {
            Toast.makeText(this, getString(R.string.itemNenhum), Toast.LENGTH_SHORT).show();
            return;
        }

        if (opcao == ACAO_QR) {
            IntentIntegrator integrator = new IntentIntegrator(this);
            integrator.setPrompt(getString(R.string.qrPrompt, bemSelecionado.patrimonio));
            integrator.setBeepEnabled(true);
            integrator.setOrientationLocked(true);
            integrator.initiateScan();

        } else if (opcao == ACAO_MANUAL) {
            final EditText etTombo = new EditText(this);
            etTombo.setText(bemSelecionado.patrimonio);
            etTombo.setHint(R.string.manualHint);

            new AlertDialog.Builder(this)
                    .setTitle(R.string.manualTitulo)
                    .setMessage(R.string.manualMensagem)
                    .setView(Dialogos.comMargem(this, etTombo))
                    .setPositiveButton(R.string.btConfirmar, (dialog, which) -> {
                        String digitado = etTombo.getText().toString().trim();
                        if (!digitado.isEmpty()) {
                            bemSelecionado.status = "LOCALIZADO_MANUAL";
                            adicionarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "MANUAL"));
                            atualizarContadores();
                            adaptador.notifyDataSetChanged();
                            registrarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "MANUAL"));
                            // O cartão já confirma; o toast que repetia a mesma frase saiu.
                            tvItemSelecionado.setText(getString(R.string.resolvidoManual, bemSelecionado.patrimonio));
                            soltarSelecao(false);
                        }
                    })
                    .setNegativeButton(R.string.btCancelar, null)
                    .show();

        } else if (opcao == ACAO_FALTANTE) {
            new AlertDialog.Builder(this)
                    .setTitle(R.string.faltanteTitulo)
                    .setMessage(getString(R.string.faltanteMensagem, bemSelecionado.patrimonio, bemSelecionado.descricao))
                    .setPositiveButton(R.string.btMarcarFaltante, (dialog, which) -> {
                        bemSelecionado.status = "LOCALIZADO_FALTANTE";
                        adicionarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "FALTANTE"));
                        atualizarContadores();
                        adaptador.notifyDataSetChanged();
                        registrarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "FALTANTE"));
                        tvItemSelecionado.setText(getString(R.string.resolvidoFaltante, bemSelecionado.patrimonio));
                        soltarSelecao(false);
                    })
                    .setNegativeButton(R.string.btCancelar, null)
                    .show();
        }
    }

    /** Guarda a resolução na sala aberta e atualiza contagem e lista. */
    private void registrarResolucao(SyncManager.PendenciaResolvida resolucao) {
        salaAberta.resolvidas.add(resolucao);
        salaAberta.resolucoesNaoEnviadas = true;
        atualizarContadores();
        adaptador.notifyDataSetChanged();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        IntentResult result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (result != null) {
            if (result.getContents() != null && bemSelecionado != null && salaAberta != null) {
                String conteudoQr = result.getContents().trim();
                bemSelecionado.status = "LOCALIZADO_QR";
                adicionarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, conteudoQr, "QR"));
                atualizarContadores();
                adaptador.notifyDataSetChanged();
                registrarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, conteudoQr, "QR"));
                tvItemSelecionado.setText(getString(R.string.resolvidoQr, bemSelecionado.patrimonio));
                Toast.makeText(this, getString(R.string.msgQrLido, conteudoQr), Toast.LENGTH_LONG).show();
                soltarSelecao(false);
            } else {
                Toast.makeText(this, getString(R.string.msgQrCancelado), Toast.LENGTH_SHORT).show();
            }
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    private void sincronizarComServidor() {
        final ConferenciaPendente cp = getConferenciaAtiva();
        if (cp == null) {
            Toast.makeText(this, getString(R.string.pendVaziaTitulo), Toast.LENGTH_SHORT).show();
            return;
        }

        Toast.makeText(this, getString(R.string.msgEnviandoPc), Toast.LENGTH_SHORT).show();
        syncManager.sincronizarVarredura(cp.sessao, cp.varredura, cp.leituras, cp.resolucoes, sucesso -> {
        if (salaAberta == null) {
            return;
        }
        Toast.makeText(this, getString(R.string.msgEnviandoPc), Toast.LENGTH_SHORT).show();
        // Enviado ou guardado na fila, o que foi resolvido até aqui já não se perde.
        salaAberta.resolucoesNaoEnviadas = false;
        syncManager.sincronizarVarredura(salaAberta.sessao, salaAberta.varredura, salaAberta.leituras,
                new ArrayList<>(salaAberta.resolvidas), sucesso -> {
            if (sucesso) {
                Toast.makeText(this, getString(R.string.msgEnviadoPc), Toast.LENGTH_LONG).show();
                syncManager.sincronizarFilaOffline(filaSucesso -> {
                    verificarConexaoServidor();
                });
            } else {
                Toast.makeText(this, getString(R.string.msgEnvioNaFila), Toast.LENGTH_LONG).show();
                verificarConexaoServidor();
            }

            conferenciasPendentes.remove(cp.sala);
            salvarConferenciasNoDisco(TagsSalaActivity.this);

            if (!conferenciasPendentes.isEmpty()) {
                salaAtiva = conferenciasPendentes.keySet().iterator().next();
            } else {
                salaAtiva = "";
            }

            atualizarTela();
        });
    }

    private String formatarStatus(String status) {
        if ("LOCALIZADO_RFID".equals(status)) return getString(R.string.statusRfid);
        if ("LOCALIZADO_QR".equals(status)) return getString(R.string.statusQr);
        if ("LOCALIZADO_MANUAL".equals(status)) return getString(R.string.statusManual);
        if ("LOCALIZADO_FALTANTE".equals(status) || "FALTANTE".equals(status)) return getString(R.string.statusFaltante);
        return getString(R.string.statusPendente);
    }

    private static int[] estiloDaSituacao(String status) {
        if ("LOCALIZADO_RFID".equals(status)) {
            return new int[]{R.string.situacaoRfid, R.color.sucesso_fundo, R.color.sucesso_texto, R.color.sucesso};
        }
        if ("LOCALIZADO_QR".equals(status)) {
            return new int[]{R.string.situacaoQr, R.color.info_fundo, R.color.info_texto, R.color.info};
        }
        if ("LOCALIZADO_MANUAL".equals(status)) {
            return new int[]{R.string.situacaoManual, R.color.ciano_fundo, R.color.ciano_texto, R.color.ciano};
        }
        if ("LOCALIZADO_FALTANTE".equals(status) || "FALTANTE".equals(status)) {
            return new int[]{R.string.situacaoFaltante, R.color.erro_fundo, R.color.erro_texto, R.color.erro};
        }
        return new int[]{R.string.situacaoPendente, R.color.alerta_fundo, R.color.alerta_texto, R.color.alerta};
    }

    private class BensAdapter extends ArrayAdapter<SyncManager.BemEsperado> {
        public BensAdapter(List<SyncManager.BemEsperado> itens) {
            super(TagsSalaActivity.this, R.layout.item_bem, itens);
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            if (convertView == null) {
                convertView = LayoutInflater.from(getContext()).inflate(R.layout.item_bem, parent, false);
            }

            SyncManager.BemEsperado bem = getItem(position);
            if (bem != null) {
                TextView titulo = convertView.findViewById(R.id.tv_bem_titulo);
                TextView detalhe = convertView.findViewById(R.id.tv_bem_detalhe);
                TextView selo = convertView.findViewById(R.id.tv_bem_status);
                View faixa = convertView.findViewById(R.id.v_bem_faixa);

                titulo.setText(getString(R.string.bemTitulo, bem.patrimonio, bem.descricao));
                String epcTxt = bem.epc != null && !bem.epc.isEmpty()
                        ? getString(R.string.bemEpc, bem.epc) : getString(R.string.bemSemEpc);
                detalhe.setText(bem.metalico ? getString(R.string.bemMetalico, epcTxt) : epcTxt);

                int[] estilo = estiloDaSituacao(bem.status);
                selo.setText(estilo[0]);
                selo.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(getContext(), estilo[1])));
                selo.setTextColor(ContextCompat.getColor(getContext(), estilo[2]));
                faixa.setBackgroundColor(ContextCompat.getColor(getContext(), estilo[3]));

                convertView.setBackgroundColor(ContextCompat.getColor(getContext(),
                        bem == bemSelecionado ? R.color.fiea_azul_fundo : R.color.superficie));
            }

            return convertView;
        }
    }

    // Adaptador da lista de salas: quanto falta em cada uma, com o mesmo selo dos bens.
    private class SalasAdapter extends ArrayAdapter<SalaConferida> {
        private final SimpleDateFormat formatoHora = new SimpleDateFormat("HH:mm", Locale.getDefault());

        SalasAdapter(List<SalaConferida> salas) {
            super(TagsSalaActivity.this, R.layout.item_sala, salas);
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            if (convertView == null) {
                convertView = LayoutInflater.from(getContext()).inflate(R.layout.item_sala, parent, false);
            }

            SalaConferida sala = getItem(position);
            if (sala != null) {
                TextView nome = convertView.findViewById(R.id.tv_sala_nome);
                TextView detalhe = convertView.findViewById(R.id.tv_sala_detalhe);
                TextView selo = convertView.findViewById(R.id.tv_sala_status);
                View faixa = convertView.findViewById(R.id.v_sala_faixa);

                nome.setText(getString(R.string.pendSala, sala.varredura.sala));
                detalhe.setText(getString(R.string.salaLinha, sala.varredura.numero,
                        formatoHora.format(new Date(sala.varredura.fim > 0 ? sala.varredura.fim : sala.varredura.inicio))));

                boolean resolvida = sala.pendentes <= 0;
                selo.setText(resolvida ? getString(R.string.seloResolvida)
                        : getResources().getQuantityString(R.plurals.seloPendentes, sala.pendentes, sala.pendentes));
                selo.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(getContext(),
                        resolvida ? R.color.sucesso_fundo : R.color.alerta_fundo)));
                selo.setTextColor(ContextCompat.getColor(getContext(),
                        resolvida ? R.color.sucesso_texto : R.color.alerta_texto));
                faixa.setBackgroundColor(ContextCompat.getColor(getContext(),
                        resolvida ? R.color.sucesso : R.color.alerta));
            }

            return convertView;
        }
    }
}
