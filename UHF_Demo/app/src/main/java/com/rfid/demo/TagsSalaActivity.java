package com.rfid.demo;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
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

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Tela de Conferência e Resolução de Pendências (RF09 e RF10).
 * Substitui a antiga tela de Tags de Referência.
 *
 * Permite ao operador inspecionar os bens da sala que não foram lidos por RFID e
 * resolvê-los por:
 * 1. Leitura de QR Code com a câmera traseira
 * 2. Conferência manual com digitação do tombo
 * 3. Marcação formal de Item Faltante / Desaparecido
 */
public class TagsSalaActivity extends Activity implements View.OnClickListener {

    // Dados compartilhados vindos da varredura
    public static Sessao sessaoVarredura = null;
    public static Varredura varreduraConcluida = null;
    public static List<Leitura> leiturasVarredura = new ArrayList<>();
    public static Set<String> epcsLidosVarredura = new HashSet<>();
    public static String salaAtiva = "";

    // Métodos de resolução (RF10), um botão para cada
    private static final int ACAO_QR = 0;
    private static final int ACAO_MANUAL = 1;
    private static final int ACAO_FALTANTE = 2;

    private TextView tvPendSala, tvPendStatus, tvItemSelecionado, tvSyncStatus;
    private TextView tvContRfid, tvContQr, tvContManual, tvContFaltante, tvContPendente;
    private LinearProgressIndicator pbPendencias;
    private View grupoSemVarredura, grupoConferencia;
    private Button btResolverQr, btResolverManual, btResolverFaltante, btVoltarLista;
    private Button btnSincronizarServidor, btIrInventario;
    private ListView lvPendencias;

    private final List<SyncManager.BemEsperado> listaBens = new ArrayList<>();
    private final List<SyncManager.PendenciaResolvida> pendenciasResolvidas = new ArrayList<>();
    private SyncManager.BemEsperado bemSelecionado = null;

    // Varredura em tela. As statics acima trocam quando chega outra sala; estas guardam
    // a anterior, para que o que foi resolvido nela seja enviado com ela, e não com a nova.
    private Sessao sessaoExibida = null;
    private Varredura varreduraExibida = null;
    private List<Leitura> leiturasExibidas = new ArrayList<>();
    private boolean resolucoesNaoEnviadas = false;
    private BensAdapter adaptador;
    private SyncManager syncManager;

    public static void carregarParaConferencia(Sessao s, Varredura v, List<Leitura> leituras, Set<String> epcs, String sala) {
        sessaoVarredura = s;
        varreduraConcluida = v;
        leiturasVarredura = leituras != null ? new ArrayList<>(leituras) : new ArrayList<Leitura>();
        epcsLidosVarredura = epcs != null ? new HashSet<>(epcs) : new HashSet<String>();
        salaAtiva = sala != null ? sala : "";
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_tags_sala);

        syncManager = SyncManager.getInstance(this);

        tvPendSala = findViewById(R.id.tv_pend_sala);
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
        grupoConferencia = findViewById(R.id.grupo_conferencia);

        btResolverQr = findViewById(R.id.bt_resolver_qr);
        btResolverManual = findViewById(R.id.bt_resolver_manual);
        btResolverFaltante = findViewById(R.id.bt_resolver_faltante);
        btVoltarLista = findViewById(R.id.bt_voltar_lista);
        btnSincronizarServidor = findViewById(R.id.btn_sincronizar_servidor);
        btIrInventario = findViewById(R.id.bt_ir_inventario);
        lvPendencias = findViewById(R.id.lv_pendencias);

        btResolverQr.setOnClickListener(this);
        btResolverManual.setOnClickListener(this);
        btResolverFaltante.setOnClickListener(this);
        btVoltarLista.setOnClickListener(this);
        btnSincronizarServidor.setOnClickListener(this);
        btIrInventario.setOnClickListener(this);

        adaptador = new BensAdapter(listaBens);
        lvPendencias.setAdapter(adaptador);

        lvPendencias.setOnItemClickListener((parent, view, position, id) -> {
            if (position < listaBens.size()) {
                bemSelecionado = listaBens.get(position);
                mostrarSelecionado();
                adaptador.notifyDataSetChanged();
            }
        });
        mostrarSelecionado();
    }

    /** Texto do bem escolhido; as ações só ficam ativas com um bem selecionado. */
    private void mostrarSelecionado() {
        boolean selecionado = bemSelecionado != null;
        if (selecionado) {
            tvItemSelecionado.setText(getString(R.string.itemSelecionado, bemSelecionado.patrimonio,
                    bemSelecionado.descricao, formatarStatus(bemSelecionado.status)));
        }
        btResolverQr.setEnabled(selecionado);
        btResolverManual.setEnabled(selecionado);
        btResolverFaltante.setEnabled(selecionado);
        btVoltarLista.setVisibility(selecionado ? View.VISIBLE : View.GONE);
    }

    /** Botão "Voltar à lista" e tecla Voltar (MainActivity): solta o bem selecionado. */
    public boolean voltarParaLista() {
        if (bemSelecionado == null) {
            return false;
        }
        soltarSelecao(true);
        return true;
    }

    /**
     * Solta a seleção e mostra a lista do começo, com os pendentes em cima. Depois de
     * uma resolução a mensagem de confirmação fica no cartão (limparMensagem = false).
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

    /**
     * Chegou outra varredura (ScanActivity.carregarParaConferencia). As resoluções e a
     * seleção eram da sala anterior: as que ainda não tinham ido ao PC seguem agora com
     * a varredura delas (ou para a fila), e a lista recomeça vazia para a sala nova.
     */
    private void trocarDeVarredura() {
        if (resolucoesNaoEnviadas && varreduraExibida != null) {
            final String salaAnterior = varreduraExibida.sala;
            syncManager.sincronizarVarredura(sessaoExibida, varreduraExibida, leiturasExibidas,
                    new ArrayList<>(pendenciasResolvidas), sucesso -> Toast.makeText(this,
                            getString(sucesso ? R.string.msgResolucoesAnterioresEnviadas
                                    : R.string.msgResolucoesAnterioresNaFila, salaAnterior),
                            Toast.LENGTH_LONG).show());
        }
        sessaoExibida = sessaoVarredura;
        varreduraExibida = varreduraConcluida;
        leiturasExibidas = leiturasVarredura;
        pendenciasResolvidas.clear();
        resolucoesNaoEnviadas = false;
        soltarSelecao(true);
    }

    // Pendentes primeiro: é o que o operador ainda precisa resolver. A ordenação é
    // estável, então dentro de cada grupo vale a ordem que veio do PC.
    private void ordenarPendentesPrimeiro() {
        Collections.sort(listaBens, (a, b) -> Boolean.compare(!estaPendente(a), !estaPendente(b)));
    }

    @Override
    protected void onResume() {
        super.onResume();
        atualizarTela();
        verificarConexaoServidor();
    }

    private void atualizarTela() {
        String sala = salaAtiva != null && !salaAtiva.isEmpty() ? salaAtiva : (varreduraConcluida != null ? varreduraConcluida.sala : "");
        // Sem varredura encerrada, a tela inteira vira a explicação de como chegar aqui.
        boolean semVarredura = sala.isEmpty();
        grupoSemVarredura.setVisibility(semVarredura ? View.VISIBLE : View.GONE);
        grupoConferencia.setVisibility(semVarredura ? View.GONE : View.VISIBLE);
        if (semVarredura) {
            return;
        }
        if (varreduraConcluida != varreduraExibida) {
            trocarDeVarredura();
        }

        tvPendSala.setText(getString(R.string.pendSala, sala));

        // Carrega bens da sala via SyncManager (rede ou cache offline)
        syncManager.buscarBensDaSala(sala, bens -> {
            listaBens.clear();
            if (bens != null) {
                for (SyncManager.BemEsperado b : bens) {
                    // Cruzamento RF09: Se o EPC foi lido via RFID na varredura, marca como LOCALIZADO_RFID
                    if (b.epc != null && !b.epc.isEmpty() && epcsLidosVarredura.contains(b.epc.toUpperCase())) {
                        b.status = "LOCALIZADO_RFID";
                    }
                    // Mantém resoluções já feitas nesta tela
                    for (SyncManager.PendenciaResolvida pr : pendenciasResolvidas) {
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
        pbPendencias.setMax(Math.max(total, 1));
        pbPendencias.setProgressCompat(total - pendentes, true);
        tvContRfid.setText(String.valueOf(rfid));
        tvContQr.setText(String.valueOf(qr));
        tvContManual.setText(String.valueOf(manual));
        tvContFaltante.setText(String.valueOf(faltante));
        tvContPendente.setText(String.valueOf(pendentes));
    }

    /** Mesmo critério de atualizarContadores: tudo que não foi resolvido de algum jeito. */
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

    // O endereço do PC se configura na barra superior (MainActivity), que vale para
    // todas as abas; o botão "Configurar PC" que havia aqui repetia isso.
    @Override
    public void onClick(View v) {
        if (v == btResolverQr) {
            executarAcao(ACAO_QR);
        } else if (v == btResolverManual) {
            executarAcao(ACAO_MANUAL);
        } else if (v == btResolverFaltante) {
            executarAcao(ACAO_FALTANTE);
        } else if (v == btVoltarLista) {
            voltarParaLista();
        } else if (v == btnSincronizarServidor) {
            sincronizarComServidor();
        } else if (v == btIrInventario && MainActivity.myTabHost != null) {
            MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_INVENTARIO);
        }
    }

    private void executarAcao(int opcao) {
        if (bemSelecionado == null) {
            Toast.makeText(this, getString(R.string.itemNenhum), Toast.LENGTH_SHORT).show();
            return;
        }

        if (opcao == ACAO_QR) {
            // [1] Ler QR Code (Câmera) via ZXing (RF10)
            IntentIntegrator integrator = new IntentIntegrator(this);
            integrator.setPrompt(getString(R.string.qrPrompt, bemSelecionado.patrimonio));
            integrator.setBeepEnabled(true);
            integrator.setOrientationLocked(true);
            integrator.initiateScan();

        } else if (opcao == ACAO_MANUAL) {
            // [2] Conferência Manual (RF10)
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
                            pendenciasResolvidas.add(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "MANUAL"));
                            resolucoesNaoEnviadas = true;
                            atualizarContadores();
                            adaptador.notifyDataSetChanged();
                            // O cartão já confirma; o toast que repetia a mesma frase saiu.
                            tvItemSelecionado.setText(getString(R.string.resolvidoManual, bemSelecionado.patrimonio));
                            soltarSelecao(false);
                        }
                    })
                    .setNegativeButton(R.string.btCancelar, null)
                    .show();

        } else if (opcao == ACAO_FALTANTE) {
            // [3] Item Faltante / Desaparecido
            new AlertDialog.Builder(this)
                    .setTitle(R.string.faltanteTitulo)
                    .setMessage(getString(R.string.faltanteMensagem, bemSelecionado.patrimonio, bemSelecionado.descricao))
                    .setPositiveButton(R.string.btMarcarFaltante, (dialog, which) -> {
                        bemSelecionado.status = "LOCALIZADO_FALTANTE";
                        pendenciasResolvidas.add(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "FALTANTE"));
                        resolucoesNaoEnviadas = true;
                        atualizarContadores();
                        adaptador.notifyDataSetChanged();
                        tvItemSelecionado.setText(getString(R.string.resolvidoFaltante, bemSelecionado.patrimonio));
                        soltarSelecao(false);
                    })
                    .setNegativeButton(R.string.btCancelar, null)
                    .show();
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        IntentResult result = IntentIntegrator.parseActivityResult(requestCode, resultCode, data);
        if (result != null) {
            if (result.getContents() != null && bemSelecionado != null) {
                String conteudoQr = result.getContents().trim();
                bemSelecionado.status = "LOCALIZADO_QR";
                pendenciasResolvidas.add(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, conteudoQr, "QR"));
                resolucoesNaoEnviadas = true;
                atualizarContadores();
                adaptador.notifyDataSetChanged();
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
        Toast.makeText(this, getString(R.string.msgEnviandoPc), Toast.LENGTH_SHORT).show();
        // Enviado ou guardado na fila, o que foi resolvido até aqui já não se perde.
        resolucoesNaoEnviadas = false;
        syncManager.sincronizarVarredura(sessaoVarredura, varreduraConcluida, leiturasVarredura, pendenciasResolvidas, sucesso -> {
            if (sucesso) {
                Toast.makeText(this, getString(R.string.msgEnviadoPc), Toast.LENGTH_LONG).show();
                syncManager.sincronizarFilaOffline(filaSucesso -> {
                    verificarConexaoServidor();
                });
            } else {
                Toast.makeText(this, getString(R.string.msgEnvioNaFila), Toast.LENGTH_LONG).show();
                verificarConexaoServidor();
            }
        });
    }

    private String formatarStatus(String status) {
        if ("LOCALIZADO_RFID".equals(status)) return getString(R.string.statusRfid);
        if ("LOCALIZADO_QR".equals(status)) return getString(R.string.statusQr);
        if ("LOCALIZADO_MANUAL".equals(status)) return getString(R.string.statusManual);
        if ("LOCALIZADO_FALTANTE".equals(status) || "FALTANTE".equals(status)) return getString(R.string.statusFaltante);
        return getString(R.string.statusPendente);
    }

    /**
     * Selo de cada situação: {rótulo, fundo do selo, texto do selo, faixa lateral}.
     * Mesmas cores do relatório web; o texto usa o tom escuro, que tem contraste.
     */
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

    // Adaptador visual da lista de bens
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
                // Metálico ajuda a entender por que o RFID não leu (etiqueta sobre metal).
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
}
