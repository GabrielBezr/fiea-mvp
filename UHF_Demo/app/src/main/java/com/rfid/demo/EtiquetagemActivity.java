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

import com.rfid.demo.utils.CsvExport;
import com.rfid.demo.utils.ParEtiquetagem;
import com.rfid.demo.utils.StoragePermission;
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

    private static final int MSG_EPC_CAPTURADO = 1;

    private EditText etEpc, etPatrimonio;
    private CheckBox cbMetalico;
    private Button btLerTag, btSalvar, btLimpar;
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
        tvStatus = findViewById(R.id.tv_etiq_status);
        tvArquivo = findViewById(R.id.tv_etiq_arquivo);
        lvPares = findViewById(R.id.lv_pares);

        btLerTag.setOnClickListener(this);
        btSalvar.setOnClickListener(this);
        btLimpar.setOnClickListener(this);

        adaptador = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1, linhasDaTela);
        lvPares.setAdapter(adaptador);

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
                    pararLeitura();
                    preencherEpc(epcCapturado, ParEtiquetagem.ORIGEM_RFID);
                    etPatrimonio.requestFocus();
                }
            }
        };

        if (RFIDSDKManager.getInstance().getRfidManager() != null) {
            RFIDSDKManager.getInstance().getRfidManager().registerCallback(callback);
        }
        atualizarStatus();
    }

    @Override
    public void onClick(View v) {
        if (v == btLerTag) {
            lerTag();
        } else if (v == btSalvar) {
            salvarPar();
        } else if (v == btLimpar) {
            limparCampos();
        }
    }

    /** Arma a captura e dispara uma leitura: a primeira etiqueta vista vence. */
    private void lerTag() {
        capturandoEpc.set(true);
        int resultado = RFIDSDKManager.getInstance().getRfidManager()
                .startInventoryWithTimeout(5);
        if (resultado != 0) {
            capturandoEpc.set(false);
            avisar(R.string.msgEtiqLeituraFalhou);
        }
    }

    private void pararLeitura() {
        capturandoEpc.set(false);
        RFIDSDKManager.getInstance().getRfidManager().stopInventory();
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
        linhasDaTela.add(0, patrimonio + "  <-  " + epc
                + (par.metalico ? "  [metal]" : "")
                + (ParEtiquetagem.ORIGEM_RFID.equals(par.origemEpc) ? "" : "  [qr]"));
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
                handler.sendEmptyMessage(MSG_EPC_CAPTURADO);
            }
        }

        @Override
        public void onInventoryTagEnd() {
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // Gatilho físico do aparelho: atalho para ler a etiqueta.
        if ((keyCode == 515 || keyCode == 523) && event.getRepeatCount() == 0) {
            lerTag();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onPause() {
        super.onPause();
        pararLeitura();
    }
}
