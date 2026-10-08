package com.rfid.demo;

import android.app.Activity;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.view.KeyEvent;
import android.view.View;
import android.view.View.OnClickListener;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import com.rfid.demo.utils.CsvExport;
import com.rfid.demo.utils.StoragePermission;
import com.rfid.demo.utils.TagSala;
import com.ubx.usdk.RFIDSDKManager;
import com.ubx.usdk.rfid.aidl.IRfidCallback;

import java.io.File;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Captura das tags de referência das salas (RF01).
 *
 * São cinco por ambiente, nos quatro cantos e no centro. O arquivo gerado tem o
 * mesmo cabeçalho que o comando `inventario salas` espera, então entra no
 * cadastro direto, sem conversão.
 *
 * O EPC vem da leitura RFID ou do QR impresso na etiqueta, que carrega o mesmo
 * valor — o campo aceita as duas coisas. A alternativa seria transcrever à mão
 * 24 caracteres hexadecimais, cinco vezes por sala.
 */
public class TagsSalaActivity extends Activity implements OnClickListener {

    private static final int MSG_EPC_CAPTURADO = 1;

    private EditText etUnidade, etSala, etEpc;
    private Spinner spinnerPosicao;
    private Button btLerTag, btSalvar;
    private TextView tvStatus, tvArquivo;
    private ListView lvTags;

    private ArrayAdapter<String> adaptador;
    private final List<String> linhasDaTela = new ArrayList<>();

    // Repetição dentro da campanha é barrada aqui; a conferência contra o
    // cadastro inteiro é do computador, na importação.
    private final Set<String> epcsUsados = new HashSet<>();
    private final Set<String> posicoesUsadas = new HashSet<>();
    private int gravadas = 0;

    private final AtomicBoolean capturandoEpc = new AtomicBoolean(false);
    private volatile String epcCapturado = "";

    private File arquivo;
    private Handler handler;
    private final TagCallback callback = new TagCallback();

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(R.layout.activity_tags_sala);

        etUnidade = findViewById(R.id.et_ts_unidade);
        etSala = findViewById(R.id.et_ts_sala);
        etEpc = findViewById(R.id.et_ts_epc);
        spinnerPosicao = findViewById(R.id.spinner_posicao);
        btLerTag = findViewById(R.id.BtTsLerTag);
        btSalvar = findViewById(R.id.BtTsSalvar);
        tvStatus = findViewById(R.id.tv_ts_status);
        tvArquivo = findViewById(R.id.tv_ts_arquivo);
        lvTags = findViewById(R.id.lv_tags_sala);

        btLerTag.setOnClickListener(this);
        btSalvar.setOnClickListener(this);

        adaptador = new ArrayAdapter<>(this, android.R.layout.simple_list_item_1, linhasDaTela);
        lvTags.setAdapter(adaptador);

        arquivo = CsvExport.arquivoTagsSala(System.currentTimeMillis());

        handler = new Handler(Looper.getMainLooper()) {
            @Override
            public void handleMessage(Message msg) {
                if (msg.what == MSG_EPC_CAPTURADO) {
                    pararLeitura();
                    etEpc.setText(epcCapturado);
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
            salvarTag();
        }
    }

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

    private void salvarTag() {
        String unidade = etUnidade.getText().toString().trim();
        String sala = etSala.getText().toString().trim();
        String posicao = spinnerPosicao.getSelectedItem().toString();
        String epc = etEpc.getText().toString().trim().replaceAll("\\s", "").toUpperCase();

        if (unidade.isEmpty() || sala.isEmpty() || epc.isEmpty()) {
            avisar(R.string.msgTsCamposVazios);
            return;
        }
        if (epcsUsados.contains(epc)) {
            avisar(R.string.msgTsEpcRepetido);
            return;
        }
        String chave = sala + "|" + posicao;
        if (posicoesUsadas.contains(chave)) {
            avisar(R.string.msgTsPosicaoRepetida);
            return;
        }
        if (!StoragePermission.concedida(this)) {
            avisar(R.string.msgPermissaoExport);
            StoragePermission.solicitar(this);
            return;
        }

        // Grava antes de aceitar: se a escrita falhar, os campos continuam
        // preenchidos e nada se perde silenciosamente.
        if (!CsvExport.anexarTagSala(arquivo, new TagSala(unidade, sala, posicao, epc))) {
            avisar(R.string.msgCsvFalha);
            return;
        }

        epcsUsados.add(epc);
        posicoesUsadas.add(chave);
        gravadas++;
        linhasDaTela.add(0, sala + " / " + posicao + "  <-  " + epc);
        adaptador.notifyDataSetChanged();

        etEpc.setText("");
        avancarPosicao();
        atualizarStatus();
    }

    /**
     * Anda para a próxima posição da sala. O operador percorre os cinco pontos em
     * sequência, então deixar isso por conta dele seria pedir um toque a mais por
     * tag e abrir espaço para gravar duas no mesmo canto.
     */
    private void avancarPosicao() {
        int atual = spinnerPosicao.getSelectedItemPosition();
        int total = spinnerPosicao.getCount();
        if (atual + 1 < total) {
            spinnerPosicao.setSelection(atual + 1);
        }
    }

    private void atualizarStatus() {
        tvStatus.setText(getString(R.string.tsStatus, gravadas));
        tvArquivo.setText(gravadas == 0
                ? getString(R.string.tsSemArquivo)
                : getString(R.string.tsArquivo, arquivo.getName()));
    }

    private void avisar(int idMensagem) {
        Toast.makeText(getApplicationContext(), getString(idMensagem), Toast.LENGTH_LONG).show();
    }

    class TagCallback implements IRfidCallback {
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
