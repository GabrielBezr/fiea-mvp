package com.rfid.demo;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.res.ColorStateList;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.text.TextUtils;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.View.OnClickListener;
import android.view.ViewGroup;
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
    Button BtIniciarVarredura, BtEncerrarVarredura;
    TextView tv_count, tv_alltag, tv_tips, tv_sessao, tv_varredura;
    TextView tvChipSessao, tvChipVarredura;
    View grupoCamposSessao, grupoCamposVarredura, grupoLeitura, grupoConferindo;
    MaterialCardView cardVarredura;
    private ColorStateList corBotaoLeitura;
    ListView LvTags;
    EditText etUnidade, etOperador;
    Spinner spinnerTecnica, spinnerSala;

    // Sessão de inventário por unidade (RF04) com uma varredura por sala (RF05).
    // Paralelo a tagList, que segue alimentando a tela. onInventoryTag roda em
    // thread própria, por isso a lista é sincronizada e os estados são volatile.
    private volatile Sessao sessaoAtual = null;
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
            spinnerSala = findViewById(R.id.spinner_sala);
            carregarSalasNoSpinner();
            spinnerTecnica = findViewById(R.id.spinner_tecnica);
            spinnerTecnica.setAdapter(criarAdapterTecnicas());
            tvChipSessao = findViewById(R.id.tv_chip_sessao);
            tvChipVarredura = findViewById(R.id.tv_chip_varredura);
            grupoCamposSessao = findViewById(R.id.grupo_campos_sessao);
            grupoCamposVarredura = findViewById(R.id.grupo_campos_varredura);
            BtIniciarSessao = findViewById(R.id.BtIniciarSessao);
            BtEncerrarSessao = findViewById(R.id.BtEncerrarSessao);
            BtIniciarVarredura = findViewById(R.id.BtIniciarVarredura);
            BtEncerrarVarredura = findViewById(R.id.BtEncerrarVarredura);
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
            BtIniciarVarredura.setOnClickListener(this);
            BtEncerrarVarredura.setOnClickListener(this);
            BtExportCsv.setOnClickListener(this);
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
                        ex.toString();
                    }
                }
            };
        } catch (Exception e) {}

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
        String unidade = etUnidade.getText().toString().trim();
        String operador = etOperador.getText().toString().trim();
        if (unidade.isEmpty() || operador.isEmpty()) {
            avisar(R.string.msgSessaoCamposVazios);
            return;
        }
        sessaoAtual = new Sessao(unidade, operador, System.currentTimeMillis());
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
        ultimaSessaoEncerrada = s;
        etUnidade.setEnabled(true);
        etOperador.setEnabled(true);
        atualizarStatus();
        exportarSessao(s, true);
    }

    private void iniciarVarredura() {
        Sessao s = sessaoAtual;
        if (s == null) {
            avisar(R.string.msgSemSessao);
            return;
        }
        if (varreduraAtual != null) {
            return;
        }
        Object itemSala = spinnerSala != null ? spinnerSala.getSelectedItem() : null;
        String sala = itemSala != null ? itemSala.toString().trim() : "";
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
        if (spinnerSala != null) {
            spinnerSala.setEnabled(false);
        }
        atualizarStatus();

        // Pré-carrega bens da sala se online
        SyncManager.getInstance(this).buscarBensDaSala(sala, null);
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
        if (spinnerSala != null) {
            spinnerSala.setEnabled(true);
        }
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

        // Sem rede a busca espera o timeout antes de cair no cache; o aviso mostra
        // que o app está trabalhando e não travou.
        grupoConferindo.setVisibility(View.VISIBLE);
        SyncManager.getInstance(this).buscarBensDaSala(v.sala, bens -> {
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

        // Cada cartão mostra só o que serve no passo atual. Durante a varredura o
        // "Encerrar sessão" some, para não ser tocado por engano no meio da sala.
        boolean podeIniciarVarredura = s != null && v == null;
        grupoCamposSessao.setVisibility(s == null ? View.VISIBLE : View.GONE);
        BtIniciarSessao.setVisibility(s == null ? View.VISIBLE : View.GONE);
        BtEncerrarSessao.setVisibility(podeIniciarVarredura ? View.VISIBLE : View.GONE);
        grupoCamposVarredura.setVisibility(podeIniciarVarredura ? View.VISIBLE : View.GONE);
        BtIniciarVarredura.setVisibility(podeIniciarVarredura ? View.VISIBLE : View.GONE);
        BtEncerrarVarredura.setVisibility(v != null ? View.VISIBLE : View.GONE);
        grupoLeitura.setVisibility(v != null ? View.VISIBLE : View.GONE);

        BtIniciarSessao.setEnabled(s == null);
        BtEncerrarSessao.setEnabled(s != null);
        BtIniciarVarredura.setEnabled(s != null && v == null);
        BtEncerrarVarredura.setEnabled(v != null);
        BtExportCsv.setEnabled(ultimaVarreduraEncerrada != null);
        if (spinnerSala != null) {
            spinnerSala.setEnabled(s != null && v == null);
        }
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

    /** Encerrar exporta e fecha a sessão de vez; por isso pede confirmação. */
    private void confirmarEncerrarSessao() {
        Sessao s = sessaoAtual;
        if (s == null) {
            return;
        }
        new AlertDialog.Builder(this)
                .setTitle(R.string.tituloEncerrarSessao)
                .setMessage(getString(R.string.msgEncerrarSessao, s.unidade, s.totalVarreduras()))
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
                iniciarSessao();
            } else if (arg0 == BtEncerrarSessao) {
                confirmarEncerrarSessao();
            } else if (arg0 == BtIniciarVarredura) {
                iniciarVarredura();
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

    /** Ações de apoio ao inventário, num menu para não tirarem espaço da lista. */
    private void abrirMaisAcoes(View ancora) {
        PopupMenu menu = new PopupMenu(this, ancora);
        menu.inflate(R.menu.acoes_inventario);
        // Os botões ocultos guardam o estado de cada ação (setViewEnabled, atualizarStatus).
        menu.getMenu().findItem(R.id.acao_limpar).setEnabled(BtClear.isEnabled());
        menu.getMenu().findItem(R.id.acao_exportar_xls).setEnabled(Btimport.isEnabled());
        menu.getMenu().findItem(R.id.acao_reexportar).setEnabled(BtExportCsv.isEnabled());
        menu.setOnMenuItemClickListener(item -> {
            int id = item.getItemId();
            if (id == R.id.acao_limpar) {
                onClick(BtClear);
            } else if (id == R.id.acao_exportar_xls) {
                onClick(Btimport);
            } else if (id == R.id.acao_reexportar) {
                onClick(BtExportCsv);
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
        carregarSalasNoSpinner();
    }

    private void carregarSalasNoSpinner() {
        if (spinnerSala == null) return;
        SyncManager.getInstance(this).buscarSalasNomes(salas -> {
            List<String> lista = new ArrayList<>();
            if (salas != null && !salas.isEmpty()) {
                lista.addAll(salas);
            }
            if (lista.isEmpty()) {
                lista.add("Sala Padrão");
            }
            String selected = spinnerSala.getSelectedItem() != null ? spinnerSala.getSelectedItem().toString() : "";
            ArrayAdapter<String> adapterSalas = new ArrayAdapter<>(ScanActivity.this,
                    R.layout.item_spinner, lista);
            adapterSalas.setDropDownViewResource(R.layout.item_spinner_dropdown);
            spinnerSala.setAdapter(adapterSalas);
            if (!selected.isEmpty()) {
                int pos = lista.indexOf(selected);
                if (pos >= 0) {
                    spinnerSala.setSelection(pos);
                }
            }
        });
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