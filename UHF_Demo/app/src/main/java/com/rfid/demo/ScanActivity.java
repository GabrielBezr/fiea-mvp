package com.rfid.demo;

import android.app.Activity;
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
import android.widget.Button;
import android.widget.EditText;
import android.widget.ListView;
import android.widget.SimpleAdapter;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import com.rfid.demo.utils.CsvExport;
import com.rfid.demo.utils.FileImport;
import com.rfid.demo.utils.Leitura;
import com.rfid.demo.utils.Sessao;
import com.ubx.usdk.RFIDSDKManager;
import com.ubx.usdk.rfid.aidl.IRfidCallback;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;

public class ScanActivity extends Activity implements OnClickListener {

    private String TAG = ScanActivity.class.getSimpleName();
    Handler handler;
    private ArrayList<HashMap<String, String>> tagList;
    SimpleAdapter adapter;
    Button BtClear, Btimport, BtInventory;
    Button BtIniciarSessao, BtEncerrarSessao, BtExportCsv;
    TextView tv_count, tv_alltag, tv_tips, tv_sessao;
    ListView LvTags;
    EditText etSala;
    Spinner spinnerTecnica;

    // MVP: registro bruto de leituras (README T2/T3). Paralelo a tagList, que segue
    // alimentando a tela. onInventoryTag roda em thread própria, por isso a lista
    // é sincronizada e a sessão corrente é volatile.
    private volatile Sessao sessaoAtual = null;
    private Sessao ultimaSessaoEncerrada = null;
    private final List<Leitura> leiturasBrutas = Collections.synchronizedList(new ArrayList<Leitura>());

    public static String epc;
    public static String tid = "";
    private HashMap<String, String> map;
    public boolean isStopThread = false;
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
            LvTags = findViewById(R.id.LvTags);
            // Sala digitada pelo operador; técnica vem de lista fixa (arrays.xml)
            etSala = findViewById(R.id.et_sala);
            spinnerTecnica = findViewById(R.id.spinner_tecnica);
            BtIniciarSessao = findViewById(R.id.BtIniciarSessao);
            BtEncerrarSessao = findViewById(R.id.BtEncerrarSessao);
            BtExportCsv = findViewById(R.id.BtExportCsv);
            tv_sessao = findViewById(R.id.tv_sessao);

            tagList = new ArrayList<HashMap<String, String>>();

            adapter = new SimpleAdapter(this, tagList, R.layout.listtag_items,
                    new String[]{"tagUii", "tagLen", "tagCount", "tagRssi"},
                    new int[]{R.id.TvTagUii, R.id.TvTagLen, R.id.TvTagCount, R.id.TvTagRssi}) {
                @Override
                public View getView(int position, View convertView, ViewGroup parent) {
                    View view = super.getView(position, convertView, parent);
                    TextView TvTagUii = view.findViewById(R.id.TvTagUii);
                    TextView TvTagLen = view.findViewById(R.id.TvTagLen);
                    TextView TvTagCount = view.findViewById(R.id.TvTagCount);
                    TextView TvTagRssi = view.findViewById(R.id.TvTagRssi);
                    
                    TvTagUii.setTextColor(ContextCompat.getColor(ScanActivity.this, R.color.colorPrimary));
                    TvTagLen.setTextColor(ContextCompat.getColor(ScanActivity.this, R.color.colorPrimary));
                    TvTagCount.setTextColor(ContextCompat.getColor(ScanActivity.this, R.color.colorPrimary));
                    TvTagRssi.setTextColor(ContextCompat.getColor(ScanActivity.this, R.color.colorPrimary));
                    
                    return super.getView(position, convertView, parent);
                }
            };

            BtClear.setOnClickListener(this);
            Btimport.setOnClickListener(this);
            BtInventory.setOnClickListener(this);
            BtIniciarSessao.setOnClickListener(this);
            BtEncerrarSessao.setOnClickListener(this);
            BtExportCsv.setOnClickListener(this);
            atualizarStatusSessao();

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
                                updateList();
                                setViewEnabled(true);
                                BtInventory.setText(getString(R.string.btInventory));
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
            
            // INJEÇÃO DA SALA SELECIONADA PARA O CSV
            String salaAtual = etSala.getText().toString().trim();
            map.put("tagSala", salaAtual);

            map.put("tagCount", String.valueOf(1));
            map.put("tagRssi", rssi);
            CardNumber++;
            
            if (index == -1) {
                tagList.add(map);
                LvTags.setAdapter(adapter);
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
        atualizarStatusSessao();
    }

    // ---------------------------------------------------------------------
    // MVP: sessão de coleta e exportação CSV
    // ---------------------------------------------------------------------

    private void iniciarSessao() {
        if (sessaoAtual != null) {
            return;
        }
        String sala = etSala.getText().toString().trim();
        if (sala.isEmpty()) {
            Toast.makeText(getApplicationContext(), getString(R.string.msgSalaVazia), Toast.LENGTH_SHORT).show();
            return;
        }
        String tecnica = spinnerTecnica.getSelectedItem().toString();
        // Potência lida da configuração corrente do SDK, nunca fixada (decisão 5).
        int potencia = RFIDSDKManager.getInstance().getRfidManager().getOutputPower();
        leiturasBrutas.clear();
        sessaoAtual = new Sessao(sala, tecnica, potencia, System.currentTimeMillis());
        etSala.setEnabled(false);
        spinnerTecnica.setEnabled(false);
        atualizarStatusSessao();
    }

    private void encerrarSessao() {
        Sessao s = sessaoAtual;
        if (s == null) {
            return;
        }
        // Garante que nenhuma leitura entra depois da marcação de fim.
        if (!BtInventory.getText().equals(getString(R.string.btInventory))) {
            stopInventory();
        }
        s.fim = System.currentTimeMillis();
        sessaoAtual = null;
        ultimaSessaoEncerrada = s;
        etSala.setEnabled(true);
        spinnerTecnica.setEnabled(true);
        atualizarStatusSessao();
        exportarCsv();
    }

    /** Exporta a última sessão encerrada. Pode ser repetido (ex.: falha de escrita). */
    private void exportarCsv() {
        Sessao s = ultimaSessaoEncerrada;
        if (s == null) {
            Toast.makeText(getApplicationContext(), getString(R.string.msgSessaoNaoEncerrada), Toast.LENGTH_SHORT).show();
            return;
        }
        List<Leitura> copia;
        synchronized (leiturasBrutas) {
            copia = new ArrayList<>(leiturasBrutas);
        }
        boolean ok = CsvExport.exportar(s, copia);
        if (ok) {
            Toast.makeText(getApplicationContext(),
                    getString(R.string.msgCsvSucesso, CsvExport.pastaDestino()), Toast.LENGTH_LONG).show();
        } else {
            Toast.makeText(getApplicationContext(), getString(R.string.msgCsvFalha), Toast.LENGTH_LONG).show();
        }
    }

    private void atualizarStatusSessao() {
        Sessao s = sessaoAtual;
        if (s != null) {
            tv_sessao.setText(getString(R.string.sessaoAtiva, s.id, s.potenciaDbm, leiturasBrutas.size()));
        } else if (ultimaSessaoEncerrada != null) {
            tv_sessao.setText(getString(R.string.sessaoEncerrada, ultimaSessaoEncerrada.id, leiturasBrutas.size()));
        } else {
            tv_sessao.setText(getString(R.string.sessaoInativa));
        }
        BtIniciarSessao.setEnabled(s == null);
        BtEncerrarSessao.setEnabled(s != null);
        BtExportCsv.setEnabled(s == null && ultimaSessaoEncerrada != null);
    }

    @Override
    public void onClick(View arg0) {
        try {
            if (arg0 == BtInventory) {
                readTag();
            } else if (arg0 == BtClear) {
                clearData();
            } else if (arg0 == BtIniciarSessao) {
                iniciarSessao();
            } else if (arg0 == BtEncerrarSessao) {
                encerrarSessao();
            } else if (arg0 == BtExportCsv) {
                exportarCsv();
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
            stopInventory();
        }
    }

    private void readTag() {
        epc = "";
        if (BtInventory.getText().equals(getString(R.string.btInventory))) {
            int result = RFIDSDKManager.getInstance().getRfidManager().startInventoryWithTimeout(timeout);
            if (result == 0) {
                String content = String.format(getResources().getString(R.string.tips_inv_timeout), timeout);
                tv_tips.setText(content);
                BtInventory.setText(getString(R.string.title_stop_Inventory));
                setViewEnabled(false);
            }
        } else {
            stopInventory();
        }
    }

    private void stopInventory() {
        RFIDSDKManager.getInstance().getRfidManager().stopInventory();
        tv_tips.setText("");
    }

    class ScanCallback implements IRfidCallback {
        @Override
        public void onInventoryTag(String EPC, final String Data, final int rssi) {
            String epc = "";
            String mem = "";
            if (EPC != null) epc = EPC.toUpperCase();
            if (Data != null) mem = Data.toUpperCase();

            // MVP: registro bruto, uma entrada por evento, com o instante da leitura
            // capturado aqui (não no handler). EPC puro, sem prefixo. RSSI como o SDK
            // entrega. Só grava com sessão ativa, para toda linha ter id_sessao.
            if (sessaoAtual != null && epc.length() > 0) {
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
            Message msg = handler.obtainMessage();
            msg.what = MSG_UPDATE_STOP;
            msg.obj = "";
            handler.sendMessage(msg);
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        stopInventory();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if ((keyCode == 515 || keyCode == 523) && event.getRepeatCount() == 0) {
            if (BtInventory.getText().equals(getString(R.string.btInventory))) {
                readTag();
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        if ((keyCode == 515 || keyCode == 523) && event.getRepeatCount() == 0) {
            if (!BtInventory.getText().equals(getString(R.string.btInventory))) {
                readTag();
            }
        }
        return super.onKeyUp(keyCode, event);
    }
}