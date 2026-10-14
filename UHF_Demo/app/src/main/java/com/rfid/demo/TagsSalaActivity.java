package com.rfid.demo;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.os.Bundle;
import android.view.LayoutInflater;
import android.view.View;
import android.view.ViewGroup;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;
import com.rfid.demo.utils.Leitura;
import com.rfid.demo.utils.Sessao;
import com.rfid.demo.utils.SyncManager;
import com.rfid.demo.utils.Varredura;

import java.util.ArrayList;
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

    private TextView tvPendSala, tvPendStatus, tvItemSelecionado, tvSyncStatus;
    private Button btnConfigServidor, btnExecutarAcao, btnSincronizarServidor;
    private Spinner spinnerMetodo;
    private ListView lvPendencias;

    private final List<SyncManager.BemEsperado> listaBens = new ArrayList<>();
    private final List<SyncManager.PendenciaResolvida> pendenciasResolvidas = new ArrayList<>();
    private SyncManager.BemEsperado bemSelecionado = null;
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

        btnConfigServidor = findViewById(R.id.btn_config_servidor);
        btnExecutarAcao = findViewById(R.id.btn_executar_acao);
        btnSincronizarServidor = findViewById(R.id.btn_sincronizar_servidor);
        spinnerMetodo = findViewById(R.id.spinner_metodo_resolucao);
        lvPendencias = findViewById(R.id.lv_pendencias);

        btnConfigServidor.setOnClickListener(this);
        btnExecutarAcao.setOnClickListener(this);
        btnSincronizarServidor.setOnClickListener(this);

        // Opções do Dropbox (Spinner) solicitadas pelo usuário:
        String[] opcoesMetodo = new String[]{
                "Ler QR Code (Câmera)",
                "Conferência Manual",
                "Item Faltante / Desaparecido"
        };
        ArrayAdapter<String> spinnerAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, opcoesMetodo);
        spinnerMetodo.setAdapter(spinnerAdapter);

        adaptador = new BensAdapter(listaBens);
        lvPendencias.setAdapter(adaptador);

        lvPendencias.setOnItemClickListener((parent, view, position, id) -> {
            if (position < listaBens.size()) {
                bemSelecionado = listaBens.get(position);
                tvItemSelecionado.setText("Selecionado: " + bemSelecionado.patrimonio + " (" + bemSelecionado.descricao + ") · Status: " + formatarStatus(bemSelecionado.status));
                adaptador.notifyDataSetChanged();
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        atualizarTela();
        verificarConexaoServidor();
    }

    private void atualizarTela() {
        String sala = salaAtiva != null && !salaAtiva.isEmpty() ? salaAtiva : (varreduraConcluida != null ? varreduraConcluida.sala : "");
        if (sala.isEmpty()) {
            tvPendSala.setText("Sala: Nenhuma varredura selecionada");
            tvPendStatus.setText("Inicie e encerre uma varredura na aba Scan primeiro.");
            return;
        }

        tvPendSala.setText("Sala: " + sala);

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

        tvPendStatus.setText(String.format("Total: %d · RFID: %d · QR: %d · Manual: %d · Faltantes: %d · Pendentes: %d",
                total, rfid, qr, manual, faltante, pendentes));
    }

    private void verificarConexaoServidor() {
        tvSyncStatus.setText("Verificando servidor...");
        syncManager.testarConexao(online -> {
            int naFila = syncManager.totalPendenciasNaFila();
            if (online) {
                tvSyncStatus.setText("PC: Conectado (" + syncManager.getServerUrl() + ")" + (naFila > 0 ? " · " + naFila + " na fila" : ""));
                tvSyncStatus.setTextColor(Color.parseColor("#059669")); // Verde
            } else {
                tvSyncStatus.setText("PC: Offline (Modo offline ativo)" + (naFila > 0 ? " · " + naFila + " na fila" : ""));
                tvSyncStatus.setTextColor(Color.parseColor("#DC2626")); // Vermelho
            }
        });
    }

    @Override
    public void onClick(View v) {
        if (v == btnConfigServidor) {
            abrirConfiguracaoServidor();
        } else if (v == btnExecutarAcao) {
            executarAcaoSelecionada();
        } else if (v == btnSincronizarServidor) {
            sincronizarComServidor();
        }
    }

    private void abrirConfiguracaoServidor() {
        final EditText input = new EditText(this);
        input.setText(syncManager.getServerUrl());
        input.setHint("Ex: http://192.168.1.100:5000");

        new AlertDialog.Builder(this)
                .setTitle("Configurar Endereço do PC")
                .setMessage("Informe a URL do servidor Python na rede local:")
                .setView(input)
                .setPositiveButton("Salvar", (dialog, which) -> {
                    String url = input.getText().toString().trim();
                    if (!url.isEmpty()) {
                        syncManager.setServerUrl(url);
                        verificarConexaoServidor();
                        atualizarTela();
                    }
                })
                .setNegativeButton("Cancelar", null)
                .show();
    }

    private void executarAcaoSelecionada() {
        if (bemSelecionado == null) {
            Toast.makeText(this, "Selecione primeiro um bem na lista!", Toast.LENGTH_SHORT).show();
            return;
        }

        int opcao = spinnerMetodo.getSelectedItemPosition();

        if (opcao == 0) {
            // [1] Ler QR Code (Câmera) via ZXing (RF10)
            IntentIntegrator integrator = new IntentIntegrator(this);
            integrator.setPrompt("Aponte para o QR Code da etiqueta do bem " + bemSelecionado.patrimonio);
            integrator.setBeepEnabled(true);
            integrator.setOrientationLocked(true);
            integrator.initiateScan();

        } else if (opcao == 1) {
            // [2] Conferência Manual (RF10)
            final EditText etTombo = new EditText(this);
            etTombo.setText(bemSelecionado.patrimonio);
            etTombo.setHint("Confirme o tombo/patrimônio");

            new AlertDialog.Builder(this)
                    .setTitle("Conferência Manual")
                    .setMessage("Confirme o número de patrimônio localizado fisicamente:")
                    .setView(etTombo)
                    .setPositiveButton("Confirmar Localização", (dialog, which) -> {
                        String digitado = etTombo.getText().toString().trim();
                        if (!digitado.isEmpty()) {
                            bemSelecionado.status = "LOCALIZADO_MANUAL";
                            pendenciasResolvidas.add(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "MANUAL"));
                            atualizarContadores();
                            adaptador.notifyDataSetChanged();
                            tvItemSelecionado.setText("Bem " + bemSelecionado.patrimonio + " marcado como LOCALIZADO (MANUAL)!");
                            Toast.makeText(this, "Bem conferido manualmente!", Toast.LENGTH_SHORT).show();
                        }
                    })
                    .setNegativeButton("Cancelar", null)
                    .show();

        } else if (opcao == 2) {
            // [3] Item Faltante / Desaparecido
            new AlertDialog.Builder(this)
                    .setTitle("Item Faltante")
                    .setMessage("Confirmar que o bem " + bemSelecionado.patrimonio + " (" + bemSelecionado.descricao + ") foi procurado e NÃO está na sala?")
                    .setPositiveButton("Sim, Confirmar Faltante", (dialog, which) -> {
                        bemSelecionado.status = "LOCALIZADO_FALTANTE";
                        pendenciasResolvidas.add(new SyncManager.PendenciaResolvida(bemSelecionado.patrimonio, bemSelecionado.epc, "FALTANTE"));
                        atualizarContadores();
                        adaptador.notifyDataSetChanged();
                        tvItemSelecionado.setText("Bem " + bemSelecionado.patrimonio + " registrado como FALTANTE/DESAPARECIDO.");
                    })
                    .setNegativeButton("Cancelar", null)
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
                atualizarContadores();
                adaptador.notifyDataSetChanged();
                tvItemSelecionado.setText("Bem " + bemSelecionado.patrimonio + " LOCALIZADO via QR Code!");
                Toast.makeText(this, "QR Code lido: " + conteudoQr, Toast.LENGTH_LONG).show();
            } else {
                Toast.makeText(this, "Leitura de QR Code cancelada", Toast.LENGTH_SHORT).show();
            }
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    private void sincronizarComServidor() {
        Toast.makeText(this, "Sincronizando com o computador...", Toast.LENGTH_SHORT).show();
        syncManager.sincronizarVarredura(sessaoVarredura, varreduraConcluida, leiturasVarredura, pendenciasResolvidas, sucesso -> {
            if (sucesso) {
                Toast.makeText(this, "Varredura e pendências enviadas com sucesso ao PC!", Toast.LENGTH_LONG).show();
                syncManager.sincronizarFilaOffline(filaSucesso -> {
                    verificarConexaoServidor();
                });
            } else {
                Toast.makeText(this, "Sem conexão no momento. Dados salvos com segurança no aparelho para envio posterior!", Toast.LENGTH_LONG).show();
                verificarConexaoServidor();
            }
        });
    }

    private String formatarStatus(String status) {
        if ("LOCALIZADO_RFID".equals(status)) return "Localizado (RFID)";
        if ("LOCALIZADO_QR".equals(status)) return "Localizado (QR Code)";
        if ("LOCALIZADO_MANUAL".equals(status)) return "Localizado (Manual)";
        if ("LOCALIZADO_FALTANTE".equals(status) || "FALTANTE".equals(status)) return "Item Faltante";
        return "Pendente";
    }

    // Adaptador visual da lista de bens
    private class BensAdapter extends ArrayAdapter<SyncManager.BemEsperado> {
        public BensAdapter(List<SyncManager.BemEsperado> itens) {
            super(TagsSalaActivity.this, android.R.layout.simple_list_item_2, itens);
        }

        @Override
        public View getView(int position, View convertView, ViewGroup parent) {
            if (convertView == null) {
                convertView = LayoutInflater.from(getContext()).inflate(android.R.layout.simple_list_item_2, parent, false);
            }

            SyncManager.BemEsperado bem = getItem(position);
            TextView text1 = convertView.findViewById(android.R.id.text1);
            TextView text2 = convertView.findViewById(android.R.id.text2);

            if (bem != null) {
                text1.setText(bem.patrimonio + " - " + bem.descricao);
                text1.setTextSize(13f);
                text1.setTextColor(Color.BLACK);

                String epcTxt = bem.epc != null && !bem.epc.isEmpty() ? "EPC: " + bem.epc : "Sem EPC";
                String statusTxt = formatarStatus(bem.status);
                text2.setText(epcTxt + " · " + statusTxt);
                text2.setTextSize(11f);

                if ("LOCALIZADO_RFID".equals(bem.status)) {
                    text2.setTextColor(Color.parseColor("#059669")); // Verde
                } else if ("LOCALIZADO_QR".equals(bem.status)) {
                    text2.setTextColor(Color.parseColor("#0284C7")); // Azul
                } else if ("LOCALIZADO_MANUAL".equals(bem.status)) {
                    text2.setTextColor(Color.parseColor("#0891B2")); // Ciano
                } else if ("LOCALIZADO_FALTANTE".equals(bem.status) || "FALTANTE".equals(bem.status)) {
                    text2.setTextColor(Color.parseColor("#DC2626")); // Vermelho
                } else {
                    text2.setTextColor(Color.parseColor("#D97706")); // Âmbar / Pendente
                }

                if (bem == bemSelecionado) {
                    convertView.setBackgroundColor(Color.parseColor("#E2E8F0"));
                } else {
                    convertView.setBackgroundColor(Color.WHITE);
                }
            }

            return convertView;
        }
    }
}
