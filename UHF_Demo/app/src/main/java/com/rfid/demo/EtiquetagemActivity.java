package com.rfid.demo;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.KeyEvent;
import android.view.View;
import android.view.View.OnClickListener;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.TextView;
import android.widget.Toast;

import android.util.Log;

import com.rfid.demo.utils.CsvExport;
import com.rfid.demo.utils.ParEtiquetagem;
import com.rfid.demo.utils.StoragePermission;
import com.rfid.demo.utils.SyncManager;
import com.ubx.usdk.RFIDSDKManager;
import com.ubx.usdk.rfid.aidl.IRfidCallback;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Etiquetagem (RF03): liga o EPC da etiqueta ao número de patrimônio do bem.
 *
 * O EPC entra de duas formas. Pela leitura RFID, no botão, que é o caminho
 * normal; ou pelo QR impresso na etiqueta, que carrega o mesmo EPC e é a única
 * saída quando a etiqueta não responde ao rádio — o caso dos bens metálicos.
 *
 * O número de patrimônio é digitado ou lido pelo scanner do aparelho. O scanner
 * do DT50 em modo de emulação de teclado escreve direto no campo em foco, então
 * não há integração de SDK aqui: basta tocar no campo e apertar o gatilho.
 */
public class EtiquetagemActivity extends Activity implements OnClickListener {

    private static final String TAG = "EtiquetagemActivity";
    private static final int MSG_EPC_CAPTURADO = 1;

    private EditText etEpc, etPatrimonio;
    private CheckBox cbMetalico;
    private Button btLerTag, btSalvar, btLimpar, btnTransmitirPc;
    private final AtomicBoolean transmitindoAoPc = new AtomicBoolean(false);
    private int potenciaOriginal = -1;
    private Runnable timeoutTransmitirPc = null;
    private TextView tvStatus, tvArquivo;
    private ListView lvPares;

    private ArrayAdapter<String> adaptador;
    private final List<String> linhasDaTela = new ArrayList<>();

    // Guarda o que já entrou nesta campanha para barrar repetição na hora.
    // A conferência contra a base inteira é do computador, na importação.
    private final List<ParEtiquetagem> pares =
            Collections.synchronizedList(new ArrayList<ParEtiquetagem>());
    private final Set<String> epcsUsados = new HashSet<>();
    private final Set<String> patrimoniosUsados = new HashSet<>();

    private final AtomicBoolean capturandoEpc = new AtomicBoolean(false);
    private volatile String epcCapturado = "";
    private String origemEpc = ParEtiquetagem.ORIGEM_MANUAL;
    private boolean preenchendoCampo = false;

    private File arquivo;
    private Handler handler;
    private final EtiquetaCallback callback = new EtiquetaCallback();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_etiquetagem);

        etEpc = findViewById(R.id.et_epc);
        etPatrimonio = findViewById(R.id.et_patrimonio);
        cbMetalico = findViewById(R.id.cb_metalico);
        btLerTag = findViewById(R.id.BtLerTag);
        btSalvar = findViewById(R.id.BtSalvarPar);
        btLimpar = findViewById(R.id.BtLimparPar);
        btnTransmitirPc = findViewById(R.id.btn_transmitir_pc);
        tvStatus = findViewById(R.id.tv_etiq_status);
        tvArquivo = findViewById(R.id.tv_etiq_arquivo);
        lvPares = findViewById(R.id.lv_pares);

        btLerTag.setOnClickListener(this);
        btSalvar.setOnClickListener(this);
        btLimpar.setOnClickListener(this);
        btnTransmitirPc.setOnClickListener(this);

        adaptador = new ArrayAdapter<>(this, R.layout.item_par, linhasDaTela);
        lvPares.setAdapter(adaptador);
        lvPares.setEmptyView(findViewById(R.id.tv_pares_vazio));

        // Um EPC digitado ou vindo do QR não é o mesmo que um lido pelo rádio.
        etEpc.addTextChangedListener(new TextWatcher() {
            @Override
            public void beforeTextChanged(CharSequence s, int i, int c, int a) {
            }

            @Override
            public void onTextChanged(CharSequence s, int i, int c, int a) {
            }

            @Override
            public void afterTextChanged(Editable s) {
                if (!preenchendoCampo) {
                    origemEpc = ParEtiquetagem.ORIGEM_MANUAL;
                }
            }
        });

        arquivo = CsvExport.arquivoEtiquetagem(System.currentTimeMillis());

        handler = new Handler(Looper.getMainLooper()) {
            @Override
            public void handleMessage(Message msg) {
                if (msg.what == MSG_EPC_CAPTURADO) {
                    boolean eraTransmissaoPc = (msg.arg1 == 1);
                    String epc = epcCapturado;
                    pararLeitura();
                    preencherEpc(epc, ParEtiquetagem.ORIGEM_RFID);
                    etPatrimonio.requestFocus();

                    if (eraTransmissaoPc) {
                        SyncManager.getInstance(EtiquetagemActivity.this).enviarTagCapturada(epc, sucesso -> {
                            if (sucesso) {
                                Toast.makeText(getApplicationContext(), getString(R.string.msgTagTransmitidaSucesso, epc), Toast.LENGTH_LONG).show();
                            } else {
                                Toast.makeText(getApplicationContext(), getString(R.string.msgTagTransmitidaFalha, epc), Toast.LENGTH_LONG).show();
                            }
                        });
                    }
                }
            }
        };

        atualizarStatus();
    }

    @Override
    public void onClick(View v) {
        if (v == btLerTag) {
            lerTag();
        } else if (v == btnTransmitirPc) {
            iniciarLeituraTransmitirPc();
        } else if (v == btSalvar) {
            salvarPar();
        } else if (v == btLimpar) {
            limparCampos();
        }
    }

    /** Arma a captura e dispara uma leitura: a primeira etiqueta vista vence. */
    private void lerTag() {
        transmitindoAoPc.set(false);
        capturandoEpc.set(true);
        int resultado = RFIDSDKManager.getInstance().getRfidManager()
                .startInventoryWithTimeout(5);
        if (resultado != 0) {
            pararLeitura();
            avisar(R.string.msgEtiqLeituraFalhou);
        }
    }

    /**
     * Leitura por proximidade para cadastro no PC: reduz potência para 5 dBm
     * e garante restauração estrita para a potência original ao terminar.
     */
    private void iniciarLeituraTransmitirPc() {
        if (RFIDSDKManager.getInstance().getRfidManager() == null) {
            avisar(R.string.rfid_initialization_failure);
            return;
        }
        if (capturandoEpc.get() || transmitindoAoPc.get()) {
            pararLeitura();
            return;
        }

        try {
            potenciaOriginal = RFIDSDKManager.getInstance().getRfidManager().getOutputPower();
            Log.d(TAG, "Potência RF original gravada: " + potenciaOriginal + " dBm");
            // Reduz potência para 5 dBm para leitura estrita por aproximação
            RFIDSDKManager.getInstance().getRfidManager().setOutputPower(5);
        } catch (Exception e) {
            Log.e(TAG, "Falha ao definir potência mínima: " + e.getMessage());
        }

        transmitindoAoPc.set(true);
        capturandoEpc.set(true);
        btnTransmitirPc.setText(R.string.btTransmitindo);
        Toast.makeText(this, R.string.msgTagTransmitindo, Toast.LENGTH_SHORT).show();

        timeoutTransmitirPc = () -> {
            if (transmitindoAoPc.get()) {
                pararLeitura();
                Toast.makeText(getApplicationContext(), "Tempo esgotado. Nenhuma tag detectada.", Toast.LENGTH_SHORT).show();
            }
        };
        handler.postDelayed(timeoutTransmitirPc, 6000);

        int resultado = RFIDSDKManager.getInstance().getRfidManager()
                .startInventoryWithTimeout(5);
        if (resultado != 0) {
            pararLeitura();
            avisar(R.string.msgEtiqLeituraFalhou);
        }
    }

    private void pararLeitura() {
        if (timeoutTransmitirPc != null) {
            handler.removeCallbacks(timeoutTransmitirPc);
            timeoutTransmitirPc = null;
        }
        capturandoEpc.set(false);
        boolean eraPc = transmitindoAoPc.getAndSet(false);

        if (RFIDSDKManager.getInstance().getRfidManager() != null) {
            try {
                RFIDSDKManager.getInstance().getRfidManager().stopInventory();
            } catch (Exception ignored) {}

            // Restauração OBRIGATÓRIA da potência original
            if (eraPc && potenciaOriginal > 0) {
                try {
                    RFIDSDKManager.getInstance().getRfidManager().setOutputPower(potenciaOriginal);
                    Log.d(TAG, "Potência RF restaurada estritamente para: " + potenciaOriginal + " dBm");
                } catch (Exception e) {
                    Log.e(TAG, "Falha ao restaurar potência: " + e.getMessage());
                }
                potenciaOriginal = -1;
            }
        }
        if (btnTransmitirPc != null) {
            btnTransmitirPc.setText(R.string.btTransmitirPc);
        }
    }

    private void preencherEpc(String epc, String origem) {
        // A flag evita que o próprio preenchimento seja confundido com digitação.
        preenchendoCampo = true;
        etEpc.setText(epc);
        preenchendoCampo = false;
        origemEpc = origem;
    }

    private void salvarPar() {
        String epc = etEpc.getText().toString().trim().replaceAll("\\s", "").toUpperCase();
        String patrimonio = etPatrimonio.getText().toString().trim();

        if (epc.isEmpty() || patrimonio.isEmpty()) {
            avisar(R.string.msgEtiqCamposVazios);
            return;
        }
        if (epcsUsados.contains(epc)) {
            avisar(R.string.msgEtiqEpcRepetido);
            return;
        }
        if (patrimoniosUsados.contains(patrimonio)) {
            avisar(R.string.msgEtiqPatrimonioRepetido);
            return;
        }
        if (!StoragePermission.concedida(this)) {
            avisar(R.string.msgPermissaoExport);
            StoragePermission.solicitar(this);
            return;
        }

        ParEtiquetagem par = new ParEtiquetagem(
                System.currentTimeMillis(), epc, patrimonio, cbMetalico.isChecked(), origemEpc);

        // Grava antes de aceitar: se a escrita falhar, os campos continuam
        // preenchidos e nada se perde silenciosamente.
        if (!CsvExport.anexarEtiquetagem(arquivo, par)) {
            avisar(R.string.msgCsvFalha);
            return;
        }

        pares.add(par);
        epcsUsados.add(epc);
        patrimoniosUsados.add(patrimonio);
        // "digitado/QR": sem o rádio, o app não distingue o QR (que o leitor digita
        // no campo) de um EPC digitado à mão.
        linhasDaTela.add(0, getString(R.string.parLinha, patrimonio, epc)
                + (par.metalico ? getString(R.string.parMetalico) : "")
                + (ParEtiquetagem.ORIGEM_RFID.equals(par.origemEpc) ? "" : getString(R.string.parSemRadio)));
        adaptador.notifyDataSetChanged();

        limparCampos();
        atualizarStatus();
    }

    private void limparCampos() {
        preencherEpc("", ParEtiquetagem.ORIGEM_MANUAL);
        etPatrimonio.setText("");
        cbMetalico.setChecked(false);
        etEpc.requestFocus();
    }

    private void atualizarStatus() {
        tvStatus.setText(getString(R.string.etiqStatus, pares.size()));
        tvArquivo.setText(pares.isEmpty()
                ? getString(R.string.etiqSemArquivo)
                : getString(R.string.etiqArquivo, arquivo.getName()));
    }

    private void avisar(int idMensagem) {
        Toast.makeText(getApplicationContext(), getString(idMensagem), Toast.LENGTH_LONG).show();
    }

    class EtiquetaCallback implements IRfidCallback {
        @Override
        public void onInventoryTag(String EPC, String Data, int rssi) {
            if (EPC == null || EPC.length() == 0) {
                return;
            }
            // O callback roda em thread própria e dispara várias vezes; o
            // compareAndSet garante que só a primeira etiqueta seja aproveitada.
            if (capturandoEpc.compareAndSet(true, false)) {
                epcCapturado = EPC.toUpperCase();
                Message msg = handler.obtainMessage(MSG_EPC_CAPTURADO);
                msg.arg1 = transmitindoAoPc.get() ? 1 : 0;
                handler.sendMessage(msg);
            }
        }

        @Override
        public void onInventoryTagEnd() {
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // Gatilho físico do aparelho: atalho para ler a etiqueta.
        if (ScanActivity.isTriggerKey(keyCode) && event.getRepeatCount() == 0) {
            lerTag();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (RFIDSDKManager.getInstance().getRfidManager() != null) {
            // Regista o callback da TagsSalaActivity toda a vez que a aba for focada
            RFIDSDKManager.getInstance().getRfidManager().registerCallback(callback);
        }
    }
    
    @Override
    protected void onPause() {
        super.onPause();
        pararLeitura();
    }
}
