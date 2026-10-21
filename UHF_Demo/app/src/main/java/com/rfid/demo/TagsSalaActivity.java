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

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Tela de Conferência e Resolução de Pendências (RF09 e RF10).
 * Substitui a antiga tela de Tags de Referência.
 *
 * Dois níveis: a lista das salas que ficaram com pendências (cada varredura
 * encerrada no Inventário com algum bem não lido) e, ao tocar numa delas, os bens
 * da sala, que o operador resolve por:
 * 1. Leitura de QR Code com a câmera traseira
 * 2. Conferência manual com digitação do tombo
 * 3. Marcação formal de Item Faltante / Desaparecido
 */
public class TagsSalaActivity extends Activity implements View.OnClickListener {

    /**
     * Uma sala varrida com bens pendentes. Guarda a varredura inteira, porque as
     * resoluções vão ao PC junto com ela (SyncManager.sincronizarVarredura).
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

    // Métodos de resolução (RF10), um botão para cada
    private static final int ACAO_QR = 0;
    private static final int ACAO_MANUAL = 1;
    private static final int ACAO_FALTANTE = 2;

    private TextView tvPendSala, tvPendStatus, tvItemSelecionado, tvSyncStatus;
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
        syncManager.buscarBensDaSala(sala.varredura.sala, bens -> {
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
        // A lista de salas mostra quanto falta em cada uma.
        if (salaAberta != null) {
            salaAberta.total = total;
            salaAberta.pendentes = pendentes;
        }
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
        } else if (v == btVoltarSalas) {
            voltarParaSalas();
        } else if (v == btnSincronizarServidor) {
            sincronizarComServidor();
        } else if (v == btIrInventario && MainActivity.myTabHost != null) {
            MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_INVENTARIO);
        }
    }

    private void executarAcao(int opcao) {
        if (bemSelecionado == null || salaAberta == null) {
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
                            registrarResolucao(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "MANUAL"));
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
