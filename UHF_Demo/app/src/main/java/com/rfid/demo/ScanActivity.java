package com.rfid.demo;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.content.res.ColorStateList;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.text.TextUtils;
import android.util.Log;
import android.view.KeyEvent;
import android.view.LayoutInflater;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.View.OnClickListener;
import android.view.ViewGroup;
import android.view.inputmethod.InputMethodManager;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.PopupMenu;
import android.widget.SimpleAdapter;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import com.google.android.material.button.MaterialButton;
import com.google.android.material.card.MaterialCardView;
import com.google.android.material.progressindicator.LinearProgressIndicator;
import com.rfid.demo.utils.CsvExport;
import com.rfid.demo.utils.FileImport;
import com.rfid.demo.utils.Leitura;
import com.rfid.demo.utils.Sessao;
import com.rfid.demo.utils.StoragePermission;
import com.rfid.demo.utils.SyncManager;
import com.rfid.demo.utils.Varredura;
import com.ubx.usdk.RFIDSDKManager;
import com.ubx.usdk.rfid.aidl.IRfidCallback;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

public class ScanActivity extends Activity implements OnClickListener {

    private String TAG = ScanActivity.class.getSimpleName();
    Handler handler;
    private ArrayList<HashMap<String, String>> tagList;
    SimpleAdapter adapter;
    Button BtClear, Btimport;
    MaterialButton BtInventory;
    Button BtIniciarSessao, BtEncerrarSessao, BtExportCsv;
    Button BtEncerrarVarredura;
    TextView tv_count, tv_alltag, tv_tips, tv_sessao, tv_varredura;
    TextView tvChipSessao, tvChipVarredura;
    View grupoCamposSessao, grupoLeitura, grupoConferindo;
    MaterialCardView cardVarredura;
    // Telas do Inventário (ver atualizarStatus): sessão, salas da unidade, varredura aberta.
    View cardSessao, cardSalas, cardLista, grupoProgressoSalas, grupoSalaManual, btMaisAcoesSessao;
    TextView tvTituloSalas, tvProgressoSalas, tvSalaManualAviso;
    LinearProgressIndicator pbSalas;
    ListView lvSalasVarredura;
    EditText etSalaManual;
    Button btVarrerSala;

    /** Andamento de uma sala da unidade nesta sessão, para a lista da tela de salas. */
    private static class SalaDaSessao {
        final String nome;
        int varreduras = 0;
        int etiquetas = 0;          // EPCs distintos lidos na última varredura
        Varredura ultima = null;

        SalaDaSessao(String nome) {
            this.nome = nome;
        }
    }

    private final List<SalaDaSessao> salasDaSessao = new ArrayList<>();
    // A lista é da unidade cadastrada no PC; com unidade digitada ela mostra todas as salas
    // e o progresso some, porque "de Y salas" não teria a unidade como referência.
    private boolean salasSaoDaUnidade = false;
    // Sem nenhuma sala do PC (nem no cache), o operador digita o nome da sala.
    private boolean semSalasDoPc = false;
    private SalasAdapter adaptadorSalas;
    private ColorStateList corBotaoLeitura;
    ListView LvTags;
    EditText etUnidade, etOperador;
    Spinner spinnerTecnica, spinnerUnidade;
    TextView tvUnidadeAviso;
    // Salas do PC (ou do cache), com a unidade de cada uma. Alimentam a lista de unidades
    // e a de salas da sessão.
    private final List<SyncManager.SalaCadastrada> salasCadastradas = new ArrayList<>();

    // Sessão de inventário por unidade (RF04) com uma varredura por sala (RF05).
    // Paralelo a tagList, que segue alimentando a tela. onInventoryTag roda em
    // thread própria, por isso a lista é sincronizada e os estados são volatile.
    private volatile Sessao sessaoAtual = null;
    private static volatile Sessao sessaoAtualStatic = null;

    public static Sessao getSessaoAtual() {
        return sessaoAtualStatic;
    }

    private static volatile String unidadeSugerida = null;

    public static void setUnidadeSugerida(String unidade) {
        unidadeSugerida = unidade;
    }
    private volatile Varredura varreduraAtual = null;
    private Varredura ultimaVarreduraEncerrada = null;
    private Sessao ultimaSessaoEncerrada = null;
    private final List<Leitura> leiturasBrutas = Collections.synchronizedList(new ArrayList<Leitura>());

    public static String epc;
    public static String tid = "";
    private HashMap<String, String> map;
    public boolean isStopThread = false;
    private volatile boolean isInventoryRunning = false;
    private long triggerDownTime = 0;
    ScanCallback callback = new ScanCallback();

    private static final int MSG_UPDATE_LISTVIEW = 0;
    private static final int MSG_UPDATE_STOP = 3;
    
    public long CardNumber;
    public static List<String> mlist = new ArrayList<String>();
    public static int timeout = 300;//300 seconds
    private HashMap<String, Integer> integerHashMap = new HashMap<>();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        
        try {
            setContentView(R.layout.activity_scan);

            // Vinculação dos componentes da UI
            tv_tips = findViewById(R.id.tv_tips);
            BtClear = findViewById(R.id.BtClear);
            Btimport = findViewById(R.id.BtImport);
            tv_count = findViewById(R.id.tv_count);
            tv_alltag = findViewById(R.id.tv_alltag);
            BtInventory = findViewById(R.id.BtInventory);
            corBotaoLeitura = BtInventory.getBackgroundTintList();
            LvTags = findViewById(R.id.LvTags);
            LvTags.setEmptyView(findViewById(R.id.tv_lista_vazia));
            cardVarredura = findViewById(R.id.card_varredura);
            grupoLeitura = findViewById(R.id.grupo_leitura);
            grupoConferindo = findViewById(R.id.grupo_conferindo);
            findViewById(R.id.bt_mais_acoes).setOnClickListener(this::abrirMaisAcoes);
            etUnidade = findViewById(R.id.et_unidade);
            etOperador = findViewById(R.id.et_operador);
            spinnerUnidade = findViewById(R.id.spinner_unidade);
            tvUnidadeAviso = findViewById(R.id.tv_unidade_aviso);
            spinnerTecnica = findViewById(R.id.spinner_tecnica);
            spinnerTecnica.setAdapter(criarAdapterTecnicas());
            tvChipSessao = findViewById(R.id.tv_chip_sessao);
            tvChipVarredura = findViewById(R.id.tv_chip_varredura);
            grupoCamposSessao = findViewById(R.id.grupo_campos_sessao);
            BtIniciarSessao = findViewById(R.id.BtIniciarSessao);
            BtEncerrarSessao = findViewById(R.id.BtEncerrarSessao);
            BtEncerrarVarredura = findViewById(R.id.BtEncerrarVarredura);
            cardSessao = findViewById(R.id.card_sessao);
            cardSalas = findViewById(R.id.card_salas);
            cardLista = findViewById(R.id.card_lista);
            grupoProgressoSalas = findViewById(R.id.grupo_progresso_salas);
            tvProgressoSalas = findViewById(R.id.tv_progresso_salas);
            pbSalas = findViewById(R.id.pb_salas);
            tvTituloSalas = findViewById(R.id.tv_titulo_salas);
            findViewById(R.id.bt_mais_acoes_salas).setOnClickListener(this::abrirMaisAcoes);
            btMaisAcoesSessao = findViewById(R.id.bt_mais_acoes_sessao);
            btMaisAcoesSessao.setOnClickListener(this::abrirMaisAcoes);
            grupoSalaManual = findViewById(R.id.grupo_sala_manual);
            tvSalaManualAviso = findViewById(R.id.tv_sala_manual_aviso);
            etSalaManual = findViewById(R.id.et_sala_manual);
            btVarrerSala = findViewById(R.id.bt_varrer_sala);
            lvSalasVarredura = findViewById(R.id.lv_salas_varredura);
            adaptadorSalas = new SalasAdapter();
            lvSalasVarredura.setAdapter(adaptadorSalas);
            lvSalasVarredura.setOnItemClickListener((parent, view, position, id) ->
                    escolherSala(adaptadorSalas.getItem(position)));
            carregarSalas();
            BtExportCsv = findViewById(R.id.BtExportCsv);
            tv_sessao = findViewById(R.id.tv_sessao);
            tv_varredura = findViewById(R.id.tv_varredura);

            tagList = new ArrayList<HashMap<String, String>>();

            adapter = new SimpleAdapter(this, tagList, R.layout.listtag_items,
                    new String[]{"tagUii", "tagLen", "tagCount", "tagRssi"},
                    new int[]{R.id.TvTagUii, R.id.TvTagLen, R.id.TvTagCount, R.id.TvTagRssi}) {
                @Override
                public View getView(int position, View convertView, ViewGroup parent) {
                    View view = super.getView(position, convertView, parent);
                    TextView TvTagRssi = view.findViewById(R.id.TvTagRssi);

                    HashMap<String, String> item = position < tagList.size() ? tagList.get(position) : null;
                    String rssiStr = item != null ? item.get("tagRssi") : "";
                    boolean limitrofe = false;
                    try {
                        if (rssiStr != null && !rssiStr.isEmpty()) {
                            int rssiVal = Integer.parseInt(rssiStr.trim());
                            limitrofe = (rssiVal < 0 && rssiVal <= -70) || (rssiVal > 0 && rssiVal >= 70);
                        }
                    } catch (Exception ignored) {}

                    // Sinal limítrofe: ícone de alerta e texto âmbar escuro (o âmbar puro
                    // não tem contraste para texto pequeno sobre branco).
                    TvTagRssi.setText(rssiStr);
                    TvTagRssi.setTextColor(ContextCompat.getColor(ScanActivity.this,
                            limitrofe ? R.color.alerta_texto : R.color.texto_secundario));
                    TvTagRssi.setCompoundDrawablesRelativeWithIntrinsicBounds(
                            limitrofe ? R.drawable.ic_alerta_sinal : 0, 0, 0, 0);
                    // O ícone não tem texto; o leitor de tela precisa ouvir o alerta.
                    TvTagRssi.setContentDescription(limitrofe ? getString(R.string.descSinalFraco, rssiStr) : null);

                    // Devolve a mesma view: chamar super.getView de novo refazia o bind e
                    // apagava o destaque acima.
                    return view;
                }
            };

            BtClear.setOnClickListener(this);
            Btimport.setOnClickListener(this);
            BtInventory.setOnClickListener(this);
            BtIniciarSessao.setOnClickListener(this);
            BtEncerrarSessao.setOnClickListener(this);
            btVarrerSala.setOnClickListener(this);
            BtEncerrarVarredura.setOnClickListener(this);
            BtExportCsv.setOnClickListener(this);

            if (sessaoAtual == null && sessaoAtualStatic != null && !sessaoAtualStatic.encerrada()) {
                sessaoAtual = sessaoAtualStatic;
                if (etOperador != null && sessaoAtual.operador != null) {
                    etOperador.setText(sessaoAtual.operador);
                    etOperador.setEnabled(false);
                }
                if (etUnidade != null && sessaoAtual.unidade != null) {
                    etUnidade.setText(sessaoAtual.unidade);
                    etUnidade.setEnabled(false);
                }
            } else {
                SharedPreferences prefs = getSharedPreferences("FieaColetaPrefs", MODE_PRIVATE);
                String ultimoOp = prefs.getString("ultimo_operador", "");
                if (!ultimoOp.isEmpty() && etOperador != null && etOperador.getText().toString().isEmpty()) {
                    etOperador.setText(ultimoOp);
                }
            }
            atualizarStatus();

            if (RFIDSDKManager.getInstance().getRfidManager() != null) {
                RFIDSDKManager.getInstance().getRfidManager().registerCallback(callback);
            }

            LvTags.setAdapter(adapter);
            clearData();

            handler = new Handler(Looper.getMainLooper()) {
                @Override
                public void handleMessage(Message msg) {
                    try {
                        switch (msg.what) {
                            case MSG_UPDATE_LISTVIEW:
                                String result = msg.obj + "";
                                String[] strs = result.split(",");
                                if (strs.length == 2) {
                                    addEPCToList(strs[0], strs[1]);
                                } else {
                                    addEPCToList(strs[0] + "," + strs[1], strs[2]);
                                }
                                break;
                            case MSG_UPDATE_STOP:
                                isInventoryRunning = false;
                                updateList();
                                setViewEnabled(true);
                                BtInventory.setText(getString(R.string.btInventory));
                                mostrarEstadoLeitura(false);
                                break;
                        }
                    } catch (Exception ex) {
                        Log.e(TAG, "Falha ao atualizar a lista de leituras", ex);
                    }
                }
            };
        } catch (Exception e) {
            // Antes o erro sumia e a tela abria pela metade: botões sem ação e, sem o
            // handler, o app caía na primeira etiqueta lida. Agora ele aparece na hora.
            Log.e(TAG, "Falha ao montar a tela de Inventário", e);
            Toast.makeText(getApplicationContext(),
                    getString(R.string.msgFalhaMontarTela, e.getClass().getSimpleName()),
                    Toast.LENGTH_LONG).show();
        }

        RFIDSDKManager.getInstance().getRfidManager().setBeepEnable(true);
    }

    private void setViewEnabled(boolean enabled) {
        BtClear.setEnabled(enabled);
        if (enabled) {
            BtInventory.setEnabled(enabled);
        }
    }

    public int checkIsExist(String strEPC) {
        int existFlag = -1;
        if (strEPC == null || strEPC.length() == 0) {
            return existFlag;
        }
        if (integerHashMap.containsKey(strEPC)) {
            return integerHashMap.get(strEPC);
        }
        return existFlag;
    }

    private void clearData() {
        tv_count.setText("0");
        tv_alltag.setText("0");
        tagList.clear();
        integerHashMap.clear();
        mlist.clear();
        CardNumber = 0;
        adapter.notifyDataSetChanged();
    }

    private void addEPCToList(String rfid, String rssi) {
        if (!TextUtils.isEmpty(rfid)) {
            map = new HashMap<String, String>();
            String epc = "";
            String[] data = rfid.split(",");
            map.put("tagEpc", data[0]);
            
            if (data.length == 1) {
                epc = data[0];
            } else {
                epc = "EPC:" + data[0] + "\r\nMem:" + data[1];
            }

            int index = checkIsExist(epc);
            map.put("tagUii", epc);
            
            // Sala no .xls legado: o EPC da tag de referência da varredura corrente,
            // já que a sala deixou de ser digitada (RF01/RF05).
            Varredura vAtual = varreduraAtual;
            map.put("tagSala", vAtual == null ? "" : vAtual.sala);

            map.put("tagCount", String.valueOf(1));
            map.put("tagRssi", rssi);
            CardNumber++;
            
            if (index == -1) {
                tagList.add(map);
                // Sem setAdapter a cada etiqueta nova: o notifyDataSetChanged de
                // updateList basta, e a lista não volta ao topo nem recria as linhas.
                tv_count.setText("" + adapter.getCount());
                mlist.add(data[0]);
                integerHashMap.put(epc, mlist.size() - 1);
                updateList();
            } else {
                int tagcount = Integer.parseInt(tagList.get(index).get("tagCount"), 10) + 1;
                map.put("tagCount", String.valueOf(tagcount));
                tagList.set(index, map);
                updateList();
            }
        }
    }

    private void updateList() {
        tv_alltag.setText(String.valueOf(CardNumber));
        adapter.notifyDataSetChanged();
        atualizarStatus();
    }

    // ---------------------------------------------------------------------
    // RF04 sessão de inventário por unidade, RF05 varredura por sala,
    // RF11 tempo de cada varredura e tempo total da sessão
    // ---------------------------------------------------------------------

    private void iniciarSessao() {
        if (sessaoAtual != null) {
            return;
        }
        String unidade = unidadeEscolhida();
        String operador = etOperador.getText().toString().trim();
        if (unidade.isEmpty() || operador.isEmpty()) {
            avisar(R.string.msgSessaoCamposVazios);
            return;
        }
        getSharedPreferences("FieaColetaPrefs", MODE_PRIVATE)
                .edit()
                .putString("ultimo_operador", operador)
                .apply();
        sessaoAtual = new Sessao(unidade, operador, System.currentTimeMillis());
        sessaoAtualStatic = sessaoAtual;
        ultimaSessaoEncerrada = null;
        ultimaVarreduraEncerrada = null;
        etUnidade.setEnabled(false);
        etOperador.setEnabled(false);
        atualizarStatus();
        // Registra o início imediatamente. O arquivo será atualizado com o fim
        // quando a sessão for encerrada.
        exportarSessao(sessaoAtual, false);
    }

    private void encerrarSessao() {
        Sessao s = sessaoAtual;
        if (s == null) {
            return;
        }
        // Uma varredura aberta é encerrada e exportada antes de fechar a sessão.
        if (varreduraAtual != null) {
            encerrarVarredura();
        }
        s.fim = System.currentTimeMillis();
        sessaoAtual = null;
        sessaoAtualStatic = null;
        ultimaSessaoEncerrada = s;
        etUnidade.setEnabled(true);
        etOperador.setEnabled(true);
        atualizarStatus();
        exportarSessao(s, true);
    }

    private void iniciarVarredura(String nomeSala) {
        Sessao s = sessaoAtual;
        if (s == null) {
            avisar(R.string.msgSemSessao);
            return;
        }
        if (varreduraAtual != null) {
            return;
        }
        String sala = nomeSala != null ? nomeSala.trim() : "";
        if (sala.isEmpty()) {
            avisar(R.string.msgSalaVazia);
            return;
        }
        String tecnica = spinnerTecnica.getSelectedItem().toString();
        // Potência lida da configuração corrente do SDK, nunca fixada no código.
        int potencia = 30;
        try {
            if (RFIDSDKManager.getInstance().getRfidManager() != null) {
                potencia = RFIDSDKManager.getInstance().getRfidManager().getOutputPower();
            }
        } catch (Exception ignored) {}
        leiturasBrutas.clear();
        clearData(); // Limpa as tags exibidas na tela para a nova sala
        varreduraAtual = new Varredura(s, s.proximoNumero(), sala, tecnica, potencia,
                System.currentTimeMillis());
        spinnerTecnica.setEnabled(false);
        atualizarStatus();

        // Pré-carrega bens da sala se online
        SyncManager.getInstance(this).buscarBensDaSala(sala, s.unidade, null);
    }

    private void encerrarVarredura() {
        Varredura v = varreduraAtual;
        if (v == null) {
            return;
        }
        stopReading();
        v.fim = System.currentTimeMillis();
        varreduraAtual = null;
        ultimaVarreduraEncerrada = v;
        spinnerTecnica.setEnabled(true);
        atualizarStatus();
        exportarVarredura(v);

        // RF09: Verifica se há bens pendentes na sala
        verificarPendenciasDaSala(v);
    }

    private void verificarPendenciasDaSala(final Varredura v) {
        if (v == null) return;

        final List<Leitura> copiaLeituras;
        final Set<String> epcsLidos = new HashSet<>();
        synchronized (leiturasBrutas) {
            copiaLeituras = new ArrayList<>(leiturasBrutas);
            for (Leitura l : copiaLeituras) {
                if (l.epc != null && !l.epc.isEmpty()) {
                    epcsLidos.add(l.epc.toUpperCase());
                }
            }
        }

        // A sala passa a contar como varrida já agora, mesmo sem rede para conferir os bens.
        SalaDaSessao andamento = salaDaSessao(v.sala);
        andamento.varreduras++;
        andamento.etiquetas = epcsLidos.size();
        andamento.ultima = v;
        atualizarListaSalas();

        // Sem rede a busca espera o timeout antes de cair no cache; o aviso mostra
        // que o app está trabalhando e não travou.
        SyncManager.getInstance(this).registrarUltimaVarredura(v.sala, copiaLeituras.size(), System.currentTimeMillis());
        grupoConferindo.setVisibility(View.VISIBLE);
        SyncManager.getInstance(this).buscarBensDaSala(v.sala, v.sessao.unidade, bens -> {
            grupoConferindo.setVisibility(View.GONE);
            if (bens == null || bens.isEmpty()) {
                // Sincroniza diretamente se não há cadastro prévio de bens
                SyncManager.getInstance(ScanActivity.this).sincronizarVarredura(v.sessao, v, copiaLeituras, null, sucesso -> {
                    if (sucesso) {
                        Toast.makeText(getApplicationContext(), getString(R.string.msgVarreduraSincronizada), Toast.LENGTH_SHORT).show();
                    }
                });
                return;
            }

            int pendentesCount = 0;
            int lidosCount = 0;
            for (SyncManager.BemEsperado b : bens) {
                if (b.epc != null && !b.epc.isEmpty() && epcsLidos.contains(b.epc.toUpperCase())) {
                    lidosCount++;
                } else {
                    pendentesCount++;
                }
            }

            final int finalPendentes = pendentesCount;
            final int finalLidos = lidosCount;

            // A sala entra na lista da aba Pendências qualquer que seja a escolha no
            // aviso; antes só "Resolver agora" a levava, e quem tocava em "Depois" não
            // a encontrava mais. Sem pendências, ela não entra.
            TagsSalaActivity.carregarParaConferencia(v.sessao, v, copiaLeituras, epcsLidos,
                    bens.size(), finalPendentes);
            // O selo da sala mostra as pendências que acabaram de entrar em Pendências.
            atualizarListaSalas();

            if (finalPendentes > 0) {
                // A varredura vai ao PC (ou à fila) já agora, nas duas escolhas; as
                // resoluções seguem depois, no "Sincronizar" das Pendências.
                SyncManager.getInstance(ScanActivity.this).sincronizarVarredura(v.sessao, v, copiaLeituras, null, null);
                // Alerta ao operador conforme RF09
                new AlertDialog.Builder(ScanActivity.this)
                        .setTitle(R.string.tituloVarreduraEncerrada)
                        .setMessage(getString(R.string.msgPendenciasDetectadas, finalLidos, finalPendentes, v.sala))
                        .setPositiveButton(R.string.btResolverAgora, (dialog, which) -> {
                            TagsSalaActivity.abrirAoEntrar(v);
                            if (MainActivity.myTabHost != null) {
                                MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_PENDENCIAS);
                            }
                        })
                        .setNegativeButton(R.string.btResolverDepois, null)
                        .setCancelable(false)
                        .show();
            } else {
                Toast.makeText(getApplicationContext(), getString(R.string.msgTodosLidos, finalLidos), Toast.LENGTH_LONG).show();
                SyncManager.getInstance(ScanActivity.this).sincronizarVarredura(v.sessao, v, copiaLeituras, null, null);
            }
        });
    }

    private void exportarVarredura(Varredura v) {
        if (!permissaoDeArquivoOk()) {
            return;
        }
        List<Leitura> copia;
        synchronized (leiturasBrutas) {
            copia = new ArrayList<>(leiturasBrutas);
        }
        if (CsvExport.exportarVarredura(v, copia)) {
            Toast.makeText(getApplicationContext(),
                    getString(R.string.msgCsvSucesso, CsvExport.pastaDestino()),
                    Toast.LENGTH_LONG).show();
        } else {
            avisar(R.string.msgCsvFalha);
        }
    }

    private void exportarSessao(Sessao s, boolean notificarSucesso) {
        if (!permissaoDeArquivoOk()) {
            return;
        }
        if (CsvExport.exportarSessao(s)) {
            if (notificarSucesso) {
                Toast.makeText(getApplicationContext(),
                        getString(R.string.msgSessaoExportada, CsvExport.pastaDestino()),
                        Toast.LENGTH_LONG).show();
            }
        } else {
            avisar(R.string.msgCsvFalha);
        }
    }

    /** Reexporta a última varredura encerrada, caso a gravação tenha falhado. */
    private void reexportar() {
        Varredura v = ultimaVarreduraEncerrada;
        if (v == null) {
            avisar(R.string.msgNadaParaReexportar);
            return;
        }
        exportarVarredura(v);
        if (ultimaSessaoEncerrada != null) {
            exportarSessao(ultimaSessaoEncerrada, true);
        }
    }

    /** Rede de segurança: a permissão pode ter sido revogada depois da inicialização. */
    private boolean permissaoDeArquivoOk() {
        if (StoragePermission.concedida(this)) {
            return true;
        }
        avisar(R.string.msgPermissaoExport);
        StoragePermission.solicitar(this);
        return false;
    }

    private void avisar(int idMensagem) {
        Toast.makeText(getApplicationContext(), getString(idMensagem), Toast.LENGTH_LONG).show();
    }

    private void atualizarStatus() {
        Sessao s = sessaoAtual;
        if (s != null) {
            tv_sessao.setText(getString(R.string.sessaoAtiva, s.id, s.unidade, s.operador,
                    hora(s.inicio), s.totalVarreduras()));
            pintarSelo(tvChipSessao, R.string.chipAtiva, R.color.sucesso_fundo, R.color.sucesso_texto);
        } else if (ultimaSessaoEncerrada != null) {
            Sessao fim = ultimaSessaoEncerrada;
            tv_sessao.setText(getString(R.string.sessaoEncerrada, fim.id, fim.unidade,
                    hora(fim.inicio), hora(fim.fim),
                    fim.totalVarreduras(), duracao(fim.inicio, fim.fim)));
            pintarSelo(tvChipSessao, R.string.chipEncerrada, R.color.neutro_fundo, R.color.neutro_texto);
        } else {
            tv_sessao.setText(getString(R.string.sessaoInativa));
            tvChipSessao.setVisibility(View.GONE);
        }

        Varredura v = varreduraAtual;
        if (v != null) {
            tv_varredura.setText(getString(R.string.varreduraAtiva, v.numero, v.sala,
                    v.potenciaDbm, leiturasBrutas.size()));
            pintarSelo(tvChipVarredura, R.string.chipEmAndamento, R.color.info_fundo, R.color.info_texto);
        } else if (ultimaVarreduraEncerrada != null) {
            Varredura fim = ultimaVarreduraEncerrada;
            tv_varredura.setText(getString(R.string.varreduraEncerrada, fim.numero, fim.sala,
                    leiturasBrutas.size(), duracao(fim.inicio, fim.fim)));
            pintarSelo(tvChipVarredura, R.string.chipEncerrada, R.color.neutro_fundo, R.color.neutro_texto);
        } else {
            tv_varredura.setText(getString(s == null ? R.string.varreduraSemSessao : R.string.varreduraInativa));
            tvChipVarredura.setVisibility(View.GONE);
        }

        // A tela sai só do estado: sem sessão, os campos da sessão; com sessão, as salas da
        // unidade; com a varredura aberta, só a leitura. Da leitura só se sai encerrando a
        // varredura, então nenhuma etiqueta cai em outra sala nem a sessão é encerrada por
        // engano no meio de uma sala.
        boolean telaVarredura = v != null;
        boolean telaSalas = s != null && !telaVarredura;
        boolean telaSessao = s == null && !telaVarredura;
        cardSessao.setVisibility(telaVarredura ? View.GONE : View.VISIBLE);
        // Na tela de salas o "⋮" é o do card de salas; dois no mesmo passo confundiriam.
        btMaisAcoesSessao.setVisibility(telaSessao ? View.VISIBLE : View.GONE);
        grupoCamposSessao.setVisibility(telaSessao ? View.VISIBLE : View.GONE);
        BtIniciarSessao.setVisibility(telaSessao ? View.VISIBLE : View.GONE);
        grupoProgressoSalas.setVisibility(telaSalas ? View.VISIBLE : View.GONE);
        BtEncerrarSessao.setVisibility(telaSalas ? View.VISIBLE : View.GONE);
        cardSalas.setVisibility(telaSalas ? View.VISIBLE : View.GONE);
        cardVarredura.setVisibility(telaVarredura ? View.VISIBLE : View.GONE);
        cardLista.setVisibility(telaVarredura ? View.VISIBLE : View.GONE);
        BtEncerrarVarredura.setVisibility(v != null ? View.VISIBLE : View.GONE);
        grupoLeitura.setVisibility(v != null ? View.VISIBLE : View.GONE);

        BtIniciarSessao.setEnabled(s == null);
        BtEncerrarSessao.setEnabled(s != null);
        BtEncerrarVarredura.setEnabled(v != null);
        BtExportCsv.setEnabled(ultimaVarreduraEncerrada != null);
        if (spinnerTecnica != null) {
            spinnerTecnica.setEnabled(s != null && v == null);
        }
        BtInventory.setEnabled(s != null && v != null);
    }

    private int segundos(long inicio, long fim) {
        return fim > inicio ? (int) Math.round((fim - inicio) / 1000.0) : 0;
    }

    /** Duração legível na tela: "45 s", "3 min 20 s", "1 h 05 min". */
    private String duracao(long inicio, long fim) {
        int total = segundos(inicio, fim);
        if (total < 60) {
            return getString(R.string.duracaoSegundos, total);
        }
        if (total < 3600) {
            return getString(R.string.duracaoMinutos, total / 60, total % 60);
        }
        return getString(R.string.duracaoHoras, total / 3600, (total % 3600) / 60);
    }

    /** Hora curta para a tela. A exportação continua usando Sessao.formatarInstante. */
    private static String hora(long instante) {
        return new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date(instante));
    }

    private void pintarSelo(TextView selo, int texto, int fundo, int cor) {
        selo.setText(texto);
        selo.setTextColor(ContextCompat.getColor(this, cor));
        selo.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(this, fundo)));
        selo.setVisibility(View.VISIBLE);
    }

    /**
     * Encerrar exporta e fecha a sessão de vez; por isso pede confirmação. Com salas da
     * unidade ainda sem varredura, o aviso diz quantas e o que acontece com os bens delas.
     */
    private void confirmarEncerrarSessao() {
        Sessao s = sessaoAtual;
        if (s == null) {
            return;
        }
        String mensagem = getString(R.string.msgEncerrarSessao, s.unidade, s.totalVarreduras());
        int faltando = 0;
        for (SalaDaSessao sala : salasDaSessao) {
            if (sala.varreduras == 0) {
                faltando++;
            }
        }
        if (salasSaoDaUnidade && faltando > 0) {
            mensagem += "\n\n" + getResources().getQuantityString(R.plurals.salasFaltando, faltando, faltando);
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.tituloEncerrarSessao)
                .setMessage(mensagem)
                .setPositiveButton(R.string.btEncerrar, (dialog, which) -> encerrarSessao())
                .setNegativeButton(R.string.btCancelar, null)
                .show();
    }

    /**
     * A técnica vai para o CSV pelo código (estatica_porta, movel_ambiente); o
     * operador vê um rótulo legível. O item do adapter continua sendo o código,
     * então getSelectedItem() devolve o mesmo valor de antes.
     */
    private ArrayAdapter<String> criarAdapterTecnicas() {
        final String[] rotulos = getResources().getStringArray(R.array.spinner_tecnicas_rotulos);
        ArrayAdapter<String> adapterTecnicas = new ArrayAdapter<String>(this, R.layout.item_spinner,
                getResources().getStringArray(R.array.spinner_tecnicas)) {
            @Override
            public View getView(int position, View convertView, ViewGroup parent) {
                TextView view = (TextView) super.getView(position, convertView, parent);
                view.setText(rotulos[position]);
                return view;
            }

            @Override
            public View getDropDownView(int position, View convertView, ViewGroup parent) {
                TextView view = (TextView) super.getDropDownView(position, convertView, parent);
                view.setText(rotulos[position]);
                return view;
            }
        };
        adapterTecnicas.setDropDownViewResource(R.layout.item_spinner_dropdown);
        return adapterTecnicas;
    }

    @Override
    public void onClick(View arg0) {
        try {
            if (arg0 == BtInventory) {
                if (sessaoAtual == null) {
                    avisar(R.string.msgSemSessao);
                    return;
                }
                if (varreduraAtual == null) {
                    avisar(R.string.msgSemVarredura);
                    return;
                }
                toggleReading();
            } else if (arg0 == BtClear) {
                clearData();
            } else if (arg0 == BtIniciarSessao) {
                Sessao antes = sessaoAtual;
                iniciarSessao();
                if (antes == null && sessaoAtual != null) {
                    // Sessão nova: nenhuma sala varrida ainda. A lista sai do cache agora e
                    // é refeita quando o PC responder.
                    salasDaSessao.clear();
                    montarSalasDaSessao();
                    carregarSalas();
                }
            } else if (arg0 == BtEncerrarSessao) {
                confirmarEncerrarSessao();
            } else if (arg0 == btVarrerSala) {
                varrerSalaDigitada();
            } else if (arg0 == BtEncerrarVarredura) {
                encerrarVarredura();
            } else if (arg0 == BtExportCsv) {
                reexportar();
            } else if (arg0 == Btimport) {
                if (tagList.size() == 0) {
                    Toast.makeText(getApplicationContext(), getString(R.string.msgNodata), Toast.LENGTH_SHORT).show();
                    return;
                }
                boolean re = FileImport.daochu("", tagList);
                if (re) {
                    Toast.makeText(getApplicationContext(), getString(R.string.msgImportsuc), Toast.LENGTH_SHORT).show();
                } else {
                    Toast.makeText(getApplicationContext(), getString(R.string.msgImportfailed), Toast.LENGTH_SHORT).show();
                }
            }
        } catch (Exception e) {
            stopReading();
        }
    }

    public void registrarCallback() {
        if (RFIDSDKManager.getInstance().getRfidManager() != null) {
            RFIDSDKManager.getInstance().getRfidManager().registerCallback(callback);
            RFIDSDKManager.getInstance().enableScanHead(false);
        }
    }

    private synchronized void startReading() {
        if (isInventoryRunning) {
            return;
        }
        if (sessaoAtual == null) {
            avisar(R.string.msgSemSessao);
            return;
        }
        if (varreduraAtual == null) {
            avisar(R.string.msgSemVarredura);
            return;
        }
        if (RFIDSDKManager.getInstance().getRfidManager() == null) {
            avisar(R.string.rfid_initialization_failure);
            return;
        }
        registrarCallback();
        epc = "";
        int result = RFIDSDKManager.getInstance().getRfidManager().startInventoryWithTimeout(timeout);
        if (result == 0) {
            isInventoryRunning = true;
            String content = String.format(getResources().getString(R.string.tips_inv_timeout), timeout);
            tv_tips.setText(content);
            BtInventory.setText(getString(R.string.title_stop_Inventory));
            mostrarEstadoLeitura(true);
            setViewEnabled(false);
        } else {
            Log.e(TAG, "startInventoryWithTimeout retornou erro: " + result);
            Toast.makeText(getApplicationContext(), getString(R.string.msgFalhaIniciarLeitura, result), Toast.LENGTH_LONG).show();
        }
    }

    private synchronized void stopReading() {
        isInventoryRunning = false;
        if (RFIDSDKManager.getInstance().getRfidManager() != null) {
            RFIDSDKManager.getInstance().getRfidManager().stopInventory();
        }
        BtInventory.setText(getString(R.string.btInventory));
        mostrarEstadoLeitura(false);
        setViewEnabled(true);
    }

    /**
     * Deixa a leitura em curso impossível de não notar: botão vermelho de parar,
     * contorno azul no card e a contagem regressiva na dica. Parado, a dica volta a
     * explicar o gatilho.
     */
    private void mostrarEstadoLeitura(boolean lendo) {
        if (lendo) {
            BtInventory.setIconResource(R.drawable.ic_stop);
            BtInventory.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(this, R.color.erro)));
            cardVarredura.setStrokeColor(ContextCompat.getColor(this, R.color.fiea_azul));
            cardVarredura.setStrokeWidth(getResources().getDimensionPixelSize(R.dimen.espessura_contorno_destaque));
            tv_tips.setTextColor(ContextCompat.getColor(this, R.color.fiea_azul));
        } else {
            BtInventory.setIconResource(R.drawable.ic_contactless);
            BtInventory.setBackgroundTintList(corBotaoLeitura);
            cardVarredura.setStrokeColor(ContextCompat.getColor(this, R.color.contorno));
            cardVarredura.setStrokeWidth(getResources().getDimensionPixelSize(R.dimen.espessura_contorno));
            tv_tips.setText(R.string.dicaGatilho);
            tv_tips.setTextColor(ContextCompat.getColor(this, R.color.texto_secundario));
        }
    }

    /**
     * Ações de apoio ao inventário, num menu para não tirarem espaço da lista. Na leitura,
     * as da lista desta sala; fora dela (salas, ou sessão encerrada), as da última sala e
     * a lista do PC.
     */
    private void abrirMaisAcoes(View ancora) {
        PopupMenu menu = new PopupMenu(this, ancora);
        menu.inflate(R.menu.acoes_inventario);
        boolean lendoSala = varreduraAtual != null;
        Menu itens = menu.getMenu();
        // Os botões ocultos guardam o estado de cada ação (setViewEnabled, atualizarStatus).
        itens.findItem(R.id.acao_limpar).setVisible(lendoSala).setEnabled(BtClear.isEnabled());
        MenuItem exportarXls = itens.findItem(R.id.acao_exportar_xls).setEnabled(Btimport.isEnabled());
        if (!lendoSala) {
            // Fora da leitura, a lista em memória é a da última sala varrida.
            exportarXls.setTitle(R.string.acaoExportarXlsUltima);
        }
        itens.findItem(R.id.acao_reexportar).setVisible(!lendoSala).setEnabled(BtExportCsv.isEnabled());
        itens.findItem(R.id.acao_atualizar_salas).setVisible(!lendoSala);
        menu.setOnMenuItemClickListener(item -> {
            int id = item.getItemId();
            if (id == R.id.acao_limpar) {
                onClick(BtClear);
            } else if (id == R.id.acao_exportar_xls) {
                onClick(Btimport);
            } else if (id == R.id.acao_reexportar) {
                onClick(BtExportCsv);
            } else if (id == R.id.acao_atualizar_salas) {
                avisar(R.string.msgAtualizandoSalas);
                carregarSalas();
            }
            return true;
        });
        menu.show();
    }

    private void toggleReading() {
        if (isInventoryRunning) {
            stopReading();
        } else {
            startReading();
        }
    }

    class ScanCallback implements IRfidCallback {
        @Override
        public void onInventoryTag(String EPC, final String Data, final int rssi) {
            String epc = "";
            String mem = "";
            if (EPC != null) epc = EPC.toUpperCase();
            if (Data != null) mem = Data.toUpperCase();

            // Registro bruto, uma entrada por evento, com o instante da leitura
            // capturado aqui (não no handler). EPC puro, sem prefixo. RSSI como o SDK
            // entrega. Só grava com varredura aberta, para toda linha ter id_varredura.
            Varredura v = varreduraAtual;
            if (v != null && epc.length() > 0) {
                leiturasBrutas.add(new Leitura(System.currentTimeMillis(), epc, rssi));
            }

            Message msg = handler.obtainMessage();
            msg.what = MSG_UPDATE_LISTVIEW;
            if (mem.length() == 0)
                msg.obj = epc + "," + rssi;
            else
                msg.obj = epc + "," + mem + "," + rssi;
            handler.sendMessage(msg);
        }

        @Override
        public void onInventoryTagEnd() {
            isInventoryRunning = false;
            Message msg = handler.obtainMessage();
            msg.what = MSG_UPDATE_STOP;
            msg.obj = "";
            handler.sendMessage(msg);
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        registrarCallback();
        if (sessaoAtual == null && sessaoAtualStatic != null && !sessaoAtualStatic.encerrada()) {
            sessaoAtual = sessaoAtualStatic;
        }
        if (sessaoAtual != null) {
            if (etOperador != null && sessaoAtual.operador != null) {
                etOperador.setText(sessaoAtual.operador);
                etOperador.setEnabled(false);
            }
            if (etUnidade != null && sessaoAtual.unidade != null) {
                etUnidade.setText(sessaoAtual.unidade);
                etUnidade.setEnabled(false);
            }
            atualizarStatus();
        } else if (unidadeSugerida != null && !unidadeSugerida.isEmpty()) {
            if (spinnerUnidade != null && spinnerUnidade.getVisibility() == View.VISIBLE) {
                for (int i = 0; i < spinnerUnidade.getCount(); i++) {
                    if (unidadeSugerida.equalsIgnoreCase(String.valueOf(spinnerUnidade.getItemAtPosition(i)))) {
                        spinnerUnidade.setSelection(i);
                        break;
                    }
                }
            } else if (etUnidade != null) {
                etUnidade.setText(unidadeSugerida);
            }
            unidadeSugerida = null;
        }
        // Na volta de Pendências, o selo de cada sala já mostra o que foi resolvido lá.
        atualizarListaSalas();
        carregarSalas();
    }

    /** Busca as salas no PC (ou no cache) e refaz a lista de unidades e a de salas. */
    private void carregarSalas() {
        if (spinnerUnidade == null || adaptadorSalas == null) return;
        SyncManager.getInstance(this).buscarSalas(salas -> {
            salasCadastradas.clear();
            if (salas != null) {
                salasCadastradas.addAll(salas);
            }
            preencherUnidades();
            montarSalasDaSessao();
        });
    }

    /**
     * A unidade vem da lista do PC: digitada, um "Ufal" no lugar de "UFAL" deixava a
     * sessão sem nenhum bem no relatório. Sem lista (nunca falou com o PC), o campo de
     * texto volta, com aviso para digitar como está cadastrada.
     */
    private void preencherUnidades() {
        Set<String> unidades = new java.util.TreeSet<>();
        for (SyncManager.SalaCadastrada s : salasCadastradas) {
            if (!s.unidade.isEmpty()) {
                unidades.add(s.unidade);
            }
        }
        boolean temLista = !unidades.isEmpty();
        spinnerUnidade.setVisibility(temLista ? View.VISIBLE : View.GONE);
        etUnidade.setVisibility(temLista ? View.GONE : View.VISIBLE);
        tvUnidadeAviso.setVisibility(temLista ? View.GONE : View.VISIBLE);
        if (temLista) {
            preencherSpinner(spinnerUnidade, new ArrayList<>(unidades));
        }
    }

    /** Unidade da nova sessão: a escolhida na lista ou, sem lista, a digitada. */
    private String unidadeEscolhida() {
        if (spinnerUnidade.getVisibility() == View.VISIBLE && spinnerUnidade.getSelectedItem() != null) {
            return spinnerUnidade.getSelectedItem().toString().trim();
        }
        return etUnidade.getText().toString().trim();
    }

    /**
     * Salas da unidade da sessão, com o andamento de cada uma. Unidade fora do cadastro
     * (digitada) mostra todas, como antes; sem cadastro nenhum, o operador digita a sala.
     * Refazer a lista (o PC respondeu, "Atualizar salas") não perde o que já foi varrido.
     */
    private void montarSalasDaSessao() {
        Sessao s = sessaoAtual;
        if (s == null) {
            return;
        }
        List<String> daUnidade = new ArrayList<>();
        List<String> todas = new ArrayList<>();
        for (SyncManager.SalaCadastrada sala : salasCadastradas) {
            if (sala.unidade.equals(s.unidade) && !daUnidade.contains(sala.nome)) {
                daUnidade.add(sala.nome);
            }
            if (!todas.contains(sala.nome)) {
                todas.add(sala.nome);
            }
        }
        salasSaoDaUnidade = !daUnidade.isEmpty();
        List<String> nomes = salasSaoDaUnidade ? daUnidade : todas;
        semSalasDoPc = nomes.isEmpty();

        List<SalaDaSessao> novas = new ArrayList<>();
        for (String nome : nomes) {
            SalaDaSessao existente = procurarSala(nome);
            novas.add(existente != null ? existente : new SalaDaSessao(nome));
        }
        // Sala já varrida que não está na lista (digitada, ou saiu do cadastro) continua nela.
        for (SalaDaSessao sala : salasDaSessao) {
            if (sala.varreduras > 0 && !novas.contains(sala)) {
                novas.add(sala);
            }
        }
        salasDaSessao.clear();
        salasDaSessao.addAll(novas);
        atualizarListaSalas();
    }

    private SalaDaSessao procurarSala(String nome) {
        for (SalaDaSessao sala : salasDaSessao) {
            if (sala.nome.equals(nome)) {
                return sala;
            }
        }
        return null;
    }

    /** A sala na lista desta sessão; uma sala digitada entra na lista na primeira varredura. */
    private SalaDaSessao salaDaSessao(String nome) {
        SalaDaSessao sala = procurarSala(nome);
        if (sala == null) {
            sala = new SalaDaSessao(nome);
            salasDaSessao.add(sala);
        }
        return sala;
    }

    /** Ordena (as que faltam primeiro), conta o progresso e redesenha a lista de salas. */
    private void atualizarListaSalas() {
        if (adaptadorSalas == null) {
            return;
        }
        // Ordenação estável: dentro de cada grupo fica a ordem do cadastro.
        Collections.sort(salasDaSessao, (a, b) -> Boolean.compare(a.varreduras > 0, b.varreduras > 0));
        int total = salasDaSessao.size();
        int varridas = 0;
        for (SalaDaSessao sala : salasDaSessao) {
            if (sala.varreduras > 0) {
                varridas++;
            }
        }
        Sessao s = sessaoAtual;
        tvTituloSalas.setText(s != null ? getString(R.string.tituloSalasUnidade, s.unidade) : "");
        boolean mostrarProgresso = salasSaoDaUnidade && total > 0;
        tvProgressoSalas.setVisibility(mostrarProgresso ? View.VISIBLE : View.GONE);
        pbSalas.setVisibility(mostrarProgresso ? View.VISIBLE : View.GONE);
        if (mostrarProgresso) {
            tvProgressoSalas.setText(getResources().getQuantityString(R.plurals.progressoSalas,
                    total, varridas, total));
            pbSalas.setMax(total);
            pbSalas.setProgress(varridas);
        }
        adaptadorSalas.notifyDataSetChanged();
        lvSalasVarredura.setVisibility(total > 0 ? View.VISIBLE : View.GONE);
        grupoSalaManual.setVisibility(semSalasDoPc ? View.VISIBLE : View.GONE);
        if (semSalasDoPc) {
            tvSalaManualAviso.setText(R.string.salasManualAviso);
        }
    }

    /** Toque numa sala: abre a varredura dela. Sala já varrida pede confirmação antes. */
    private void escolherSala(final SalaDaSessao sala) {
        if (sala == null || sessaoAtual == null || varreduraAtual != null) {
            return;
        }
        if (sala.varreduras == 0 || sala.ultima == null) {
            iniciarVarredura(sala.nome);
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.tituloRevarrer)
                .setMessage(getString(R.string.msgRevarrer, sala.nome, hora(sala.ultima.fim)))
                .setPositiveButton(R.string.btVarrerDeNovo, (dialog, which) -> iniciarVarredura(sala.nome))
                .setNegativeButton(R.string.btCancelar, null)
                .show();
    }

    /** Sem salas do PC: varre a sala digitada (a mesma confirmação se ela já foi varrida). */
    private void varrerSalaDigitada() {
        String nome = etSalaManual.getText().toString().trim();
        if (nome.isEmpty()) {
            avisar(R.string.msgSalaVazia);
            return;
        }
        InputMethodManager teclado = (InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
        if (teclado != null) {
            teclado.hideSoftInputFromWindow(etSalaManual.getWindowToken(), 0);
        }
        SalaDaSessao jaNaLista = procurarSala(nome);
        if (jaNaLista != null) {
            escolherSala(jaNaLista);
        } else {
            iniciarVarredura(nome);
        }
        if (varreduraAtual != null) {
            etSalaManual.setText("");
        }
    }

    /**
     * BACK com a varredura aberta: em vez de sair, pergunta se encerra a sala. Devolve
     * false quando não há varredura, para o MainActivity seguir com o voltar normal.
     */
    public boolean voltar() {
        final Varredura v = varreduraAtual;
        if (v == null) {
            return false;
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.tituloEncerrarVarredura)
                .setMessage(getString(R.string.msgEncerrarVarreduraVoltar, v.sala))
                .setPositiveButton(R.string.btEncerrar, (dialog, which) -> {
                    if (varreduraAtual == v) {
                        encerrarVarredura();
                    }
                })
                .setNegativeButton(R.string.btContinuarLendo, null)
                .show();
        return true;
    }

    /** Uma linha de item_sala.xml por sala da unidade, com o andamento nesta sessão. */
    private class SalasAdapter extends ArrayAdapter<SalaDaSessao> {
        SalasAdapter() {
            super(ScanActivity.this, R.layout.item_sala, salasDaSessao);
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            if (convertView == null) {
                convertView = LayoutInflater.from(getContext()).inflate(R.layout.item_sala, parent, false);
            }

            SalaDaSessao sala = getItem(position);
            if (sala != null) {
                TextView nome = convertView.findViewById(R.id.tv_sala_nome);
                TextView detalhe = convertView.findViewById(R.id.tv_sala_detalhe);
                TextView selo = convertView.findViewById(R.id.tv_sala_status);
                View faixa = convertView.findViewById(R.id.v_sala_faixa);

                nome.setText(getString(R.string.pendSala, sala.nome));
                if (sala.varreduras == 0 || sala.ultima == null) {
                    detalhe.setText(R.string.salaAindaNaoVarrida);
                    selo.setVisibility(View.GONE);
                    faixa.setBackgroundColor(ContextCompat.getColor(getContext(), R.color.contorno_forte));
                } else {
                    detalhe.setText(getString(R.string.salaVarridaResumo,
                            hora(sala.ultima.fim), sala.etiquetas));
                    // Pendências ainda abertas na aba Pendências; resolvidas lá, o selo muda aqui.
                    int pendentes = TagsSalaActivity.pendentesDe(sala.ultima);
                    boolean comPendencias = pendentes > 0;
                    String texto = comPendencias
                            ? getResources().getQuantityString(R.plurals.seloPendentes, pendentes, pendentes)
                            : sala.varreduras > 1 ? getString(R.string.seloVarridaVezes, sala.varreduras)
                            : getString(R.string.seloVarrida);
                    selo.setText(texto);
                    selo.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(getContext(),
                            comPendencias ? R.color.alerta_fundo : R.color.sucesso_fundo)));
                    selo.setTextColor(ContextCompat.getColor(getContext(),
                            comPendencias ? R.color.alerta_texto : R.color.sucesso_texto));
                    selo.setVisibility(View.VISIBLE);
                    faixa.setBackgroundColor(ContextCompat.getColor(getContext(),
                            comPendencias ? R.color.alerta : R.color.sucesso));
                }
            }

            return convertView;
        }
    }

    /** Troca os itens do spinner mantendo a escolha atual, se ela continuar na lista. */
    private void preencherSpinner(Spinner spinner, List<String> itens) {
        String selecionado = spinner.getSelectedItem() != null ? spinner.getSelectedItem().toString() : "";
        ArrayAdapter<String> adapterItens = new ArrayAdapter<>(ScanActivity.this, R.layout.item_spinner, itens);
        adapterItens.setDropDownViewResource(R.layout.item_spinner_dropdown);
        spinner.setAdapter(adapterItens);
        int pos = itens.indexOf(selecionado);
        if (pos >= 0) {
            spinner.setSelection(pos);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        stopReading();
    }

    public static boolean isTriggerKey(int keyCode) {
        return keyCode == 515 || keyCode == 523 || keyCode == 293 || keyCode == 294
                || keyCode == 131 || keyCode == 132 || keyCode == 133 || keyCode == 134
                || keyCode == KeyEvent.KEYCODE_BUTTON_R1 || keyCode == KeyEvent.KEYCODE_BUTTON_L1
                || keyCode == KeyEvent.KEYCODE_FOCUS;
    }

    @Override
    public boolean dispatchKeyEvent(KeyEvent event) {
        int keyCode = event.getKeyCode();
        if (isTriggerKey(keyCode)) {
            if (event.getAction() == KeyEvent.ACTION_DOWN) {
                if (event.getRepeatCount() == 0) {
                    onKeyDown(keyCode, event);
                }
                return true;
            } else if (event.getAction() == KeyEvent.ACTION_UP) {
                onKeyUp(keyCode, event);
                return true;
            }
            return true;
        }
        return super.dispatchKeyEvent(event);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (isTriggerKey(keyCode) && event.getRepeatCount() == 0) {
            if (sessaoAtual == null) {
                avisar(R.string.msgSemSessao);
                return true;
            }
            if (varreduraAtual == null) {
                avisar(R.string.msgSemVarredura);
                return true;
            }
            triggerDownTime = System.currentTimeMillis();
            if (!isInventoryRunning) {
                startReading();
            } else {
                stopReading();
            }
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        if (isTriggerKey(keyCode)) {
            long duration = System.currentTimeMillis() - triggerDownTime;
            // Se o operador segurou o gatilho (> 500ms), soltar encerra a leitura.
            // Se foi um toque rápido (<= 500ms), mantém a varredura contínua ativa.
            if (isInventoryRunning && duration > 500) {
                stopReading();
            }
            return true;
        }
        return super.onKeyUp(keyCode, event);
    }
}