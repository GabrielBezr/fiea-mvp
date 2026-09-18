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
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.ListView;
import android.widget.SimpleAdapter;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;

import com.rfid.demo.utils.FileImport;
import com.ubx.usdk.RFIDSDKManager;
import com.ubx.usdk.rfid.aidl.IRfidCallback;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;

public class ScanActivity extends Activity implements OnClickListener {

    private String TAG = ScanActivity.class.getSimpleName();
    Handler handler;
    private ArrayList<HashMap<String, String>> tagList;
    SimpleAdapter adapter;
    Button BtClear, Btimport, BtInventory;
    TextView tv_count, tv_alltag, tv_tips;
    ListView LvTags;
    Spinner spinnerSala;

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
            spinnerSala = findViewById(R.id.spinner_sala);

            // Configuração do Spinner (Seletor de Salas)
            String[] salasMock = new String[]{"Sala A", "Sala B", "Corredor", "Estoque"};
            ArrayAdapter<String> spinnerAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, salasMock);
            spinnerAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
            spinnerSala.setAdapter(spinnerAdapter);

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
        spinnerSala.setEnabled(enabled);
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
            String salaAtual = spinnerSala.getSelectedItem().toString();
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
    }

    @Override
    public void onClick(View arg0) {
        try {
            if (arg0 == BtInventory) {
                readTag();
            } else if (arg0 == BtClear) {
                clearData();
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