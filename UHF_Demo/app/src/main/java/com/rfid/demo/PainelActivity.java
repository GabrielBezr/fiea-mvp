package com.rfid.demo;

import android.app.Activity;
import android.content.res.ColorStateList;
import android.os.Bundle;
import android.view.View;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import com.google.android.material.button.MaterialButton;
import com.rfid.demo.utils.Sessao;
import com.rfid.demo.utils.SyncManager;

import java.util.ArrayList;
import java.util.List;

public class PainelActivity extends Activity {

    private TextView tvSaudacao;
    private Spinner spinnerUnidade;
    private TextView tvBens;
    private TextView tvRfidSub;

    private View cardEnviosPendentes;
    private TextView tvFilaNum;
    private TextView tvFilaStatus;

    private TextView tvChipSessao;
    private TextView tvUltimaAtividade;
    private TextView tvSessaoInfo;

    private View itemAcaoLeitura;
    private TextView tvAcaoLeituraTitulo;
    private TextView tvAcaoLeituraSub;
    private View itemAcaoPendencias;
    private View itemAcaoEtiquetagem;

    private MaterialButton btSincronizarBase;

    private final List<String> listaUnidades = new ArrayList<>();
    private ArrayAdapter<String> adapterUnidades;
    private String unidadeSelecionada = "";
    private SyncManager.ResumoPanorama ultimoPanorama;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_painel);

        tvSaudacao = findViewById(R.id.tv_painel_saudacao);
        spinnerUnidade = findViewById(R.id.spinner_painel_unidade);
        tvBens = findViewById(R.id.tv_painel_bens);
        tvRfidSub = findViewById(R.id.tv_painel_rfid_sub);

        cardEnviosPendentes = findViewById(R.id.card_envios_pendentes);
        tvFilaNum = findViewById(R.id.tv_painel_fila_num);
        tvFilaStatus = findViewById(R.id.tv_painel_fila_status);

        tvChipSessao = findViewById(R.id.tv_chip_sessao_painel);
        tvUltimaAtividade = findViewById(R.id.tv_painel_ultima_atividade);
        tvSessaoInfo = findViewById(R.id.tv_sessao_painel_info);

        itemAcaoLeitura = findViewById(R.id.item_acao_leitura);
        tvAcaoLeituraTitulo = findViewById(R.id.tv_acao_leitura_titulo);
        tvAcaoLeituraSub = findViewById(R.id.tv_acao_leitura_sub);
        itemAcaoPendencias = findViewById(R.id.item_acao_pendencias);
        itemAcaoEtiquetagem = findViewById(R.id.item_acao_etiquetagem);

        btSincronizarBase = findViewById(R.id.bt_sincronizar_base);

        configurarSpinnerUnidades();
        configurarAcoesRapidas();

        btSincronizarBase.setOnClickListener(v -> sincronizarBase(true));

        cardEnviosPendentes.setOnClickListener(v -> sincronizarFilaSeNecessario());
    }

    private void configurarSpinnerUnidades() {
        listaUnidades.clear();
        listaUnidades.add(getString(R.string.painelTodasUnidades));

        adapterUnidades = new ArrayAdapter<>(this, R.layout.item_spinner, listaUnidades);
        adapterUnidades.setDropDownViewResource(R.layout.item_spinner_dropdown);
        spinnerUnidade.setAdapter(adapterUnidades);

        spinnerUnidade.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                unidadeSelecionada = position == 0 ? "" : listaUnidades.get(position);
                atualizarMetricas(unidadeSelecionada);
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
    }

    private void configurarAcoesRapidas() {
        itemAcaoLeitura.setOnClickListener(v -> {
            if (MainActivity.myTabHost != null) {
                if (unidadeSelecionada != null && !unidadeSelecionada.isEmpty()) {
                    ScanActivity.setUnidadeSugerida(unidadeSelecionada);
                }
                MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_INVENTARIO);
            }
        });

        itemAcaoPendencias.setOnClickListener(v -> {
            if (MainActivity.myTabHost != null) {
                MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_PENDENCIAS);
            }
        });

        itemAcaoEtiquetagem.setOnClickListener(v -> {
            if (MainActivity.myTabHost != null) {
                MainActivity.myTabHost.setCurrentTab(MainActivity.ABA_ETIQUETAGEM);
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        atualizarSessaoECabecalho();
        atualizarResumoLocal();
        atualizarFilaOffline();
        atualizarUltimaVarredura();
        sincronizarBase(false);
    }

    private void atualizarSessaoECabecalho() {
        Sessao sessao = ScanActivity.getSessaoAtual();
        if (sessao != null && !sessao.encerrada()) {
            String op = (sessao.operador != null && !sessao.operador.isEmpty()) ? sessao.operador : "-";
            String un = (sessao.unidade != null && !sessao.unidade.isEmpty()) ? sessao.unidade : "-";

            tvSaudacao.setText(getString(R.string.painelSaudacao, op));

            tvChipSessao.setText(R.string.chipAtiva);
            tvChipSessao.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(this, R.color.sucesso_fundo)));
            tvChipSessao.setTextColor(ContextCompat.getColor(this, R.color.sucesso_texto));

            tvSessaoInfo.setText(getString(R.string.painelSessaoAtiva, op, un, sessao.totalVarreduras()));

            tvAcaoLeituraTitulo.setText(R.string.painelAcaoContinuarTitulo);
            tvAcaoLeituraSub.setText(getString(R.string.painelAcaoContinuarSub, op));
        } else {
            tvSaudacao.setText(getString(R.string.painelSaudacao, getString(R.string.painelOperadorPadrao)));

            tvChipSessao.setText(R.string.chipInativa);
            tvChipSessao.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(this, R.color.neutro_fundo)));
            tvChipSessao.setTextColor(ContextCompat.getColor(this, R.color.neutro_texto));

            tvSessaoInfo.setText(R.string.painelSessaoInativa);

            tvAcaoLeituraTitulo.setText(R.string.painelAcaoLeituraTitulo);
            tvAcaoLeituraSub.setText(R.string.painelAcaoLeituraSub);
        }
    }

    private void atualizarResumoLocal() {
        ultimoPanorama = SyncManager.getInstance(this).getResumoPanoramaCache();
        atualizarListaUnidades(ultimoPanorama.unidades);
        atualizarMetricas(unidadeSelecionada);
    }

    private void atualizarFilaOffline() {
        int pendentes = SyncManager.getInstance(this).totalPendenciasNaFila();
        tvFilaNum.setText(String.valueOf(pendentes));
        if (pendentes == 0) {
            tvFilaStatus.setText(R.string.painelTudoSincronizado);
            tvFilaStatus.setTextColor(ContextCompat.getColor(this, R.color.sucesso_texto));
        } else {
            tvFilaStatus.setText(R.string.painelAguardandoWifi);
            tvFilaStatus.setTextColor(ContextCompat.getColor(this, R.color.alerta_texto));
        }
    }

    private void sincronizarFilaSeNecessario() {
        int pendentes = SyncManager.getInstance(this).totalPendenciasNaFila();
        if (pendentes == 0) {
            Toast.makeText(this, R.string.painelTudoSincronizado, Toast.LENGTH_SHORT).show();
            return;
        }

        Toast.makeText(this, R.string.painelSincronizando, Toast.LENGTH_SHORT).show();
        SyncManager.getInstance(this).sincronizarFilaOffline(sucessos -> {
            atualizarFilaOffline();
            if (sucessos > 0) {
                Toast.makeText(this, getString(R.string.msgFilaSincronizada, sucessos), Toast.LENGTH_SHORT).show();
            } else {
                Toast.makeText(this, R.string.msgEnvioNaFila, Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void atualizarListaUnidades(List<String> unidades) {
        String antes = unidadeSelecionada;
        listaUnidades.clear();
        listaUnidades.add(getString(R.string.painelTodasUnidades));
        if (unidades != null) {
            for (String u : unidades) {
                if (!listaUnidades.contains(u)) {
                    listaUnidades.add(u);
                }
            }
        }
        adapterUnidades.notifyDataSetChanged();
        if (!antes.isEmpty()) {
            int pos = listaUnidades.indexOf(antes);
            if (pos >= 0) {
                spinnerUnidade.setSelection(pos);
            }
        }
    }

    private void atualizarMetricas(String unidade) {
        if (ultimoPanorama != null) {
            SyncManager.ResumoBase r = ultimoPanorama.getResumo(unidade);
            tvBens.setText(String.valueOf(r.totalBens));
            tvRfidSub.setText(getString(R.string.painelComRfidSub, r.totalRfid));
        }
    }

    private void atualizarUltimaVarredura() {
        String texto = SyncManager.getInstance(this).getUltimaVarreduraTexto(this);
        tvUltimaAtividade.setText(texto);
    }

    private void sincronizarBase(boolean manual) {
        if (manual) {
            btSincronizarBase.setEnabled(false);
            Toast.makeText(this, R.string.painelSincronizando, Toast.LENGTH_SHORT).show();
        }
        SyncManager.getInstance(this).carregarResumoPanorama(panorama -> {
            btSincronizarBase.setEnabled(true);
            ultimoPanorama = panorama;
            atualizarListaUnidades(panorama.unidades);
            atualizarMetricas(unidadeSelecionada);
            atualizarFilaOffline();

            if (manual) {
                SyncManager.ResumoBase r = panorama.getResumo(unidadeSelecionada);
                if (panorama.sucessoRede) {
                    Toast.makeText(this, getString(R.string.painelSincronizadoSucesso, r.totalSalas, r.totalBens), Toast.LENGTH_SHORT).show();
                } else {
                    Toast.makeText(this, R.string.painelSincronizadoOffline, Toast.LENGTH_SHORT).show();
                }
            }
        });
    }
}
