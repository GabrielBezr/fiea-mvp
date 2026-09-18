package com.rfid.demo.utils;

import android.os.Environment;
import android.util.Log;

import java.io.File;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Map.Entry;
import java.util.Set;
/**
 * Created by Administrator on 2018/7/25.
 */

public class FileImport {
    static String xlsFilePath = Environment.getExternalStorageDirectory()
            + "/XLSFile/";
            
    public static boolean daochu(String tmpname, ArrayList<HashMap<String, String>> lists2) {
        try {
            String file = "";
            if (tmpname.isEmpty())
                file = xlsFilePath + "Tag_"
                        + GetTimesyyyymmddhhmmss() + ".xls";
            else
                file = xlsFilePath + tmpname;
                
            File path2 = new File(xlsFilePath);
            if (path2.mkdirs()) {
            }
            
            List<Object> al22 = new ArrayList<Object>();
            List<String> al2 = new ArrayList<String>();
            
            // 1. Definição do cabeçalho do arquivo XLS/CSV
            al2.add("EPC");
            al2.add("RSSI");
            al2.add("Sala");

            al22.add(al2);
            FileXls.writeXLS(file, al22);
            List<Object> ac = new ArrayList<Object>();

            for (int i = 0; i < lists2.size(); i++) {
                List<String> al = new ArrayList<String>();
                Set<Entry<String, String>> sets = lists2.get(i).entrySet();

                // 2. Variáveis locais para armazenar os dados de cada linha
                String id = "";
                String rssi = "";
                String sala = "";

                // 3. Extração dos dados do HashMap
                for (Entry<String, String> entry : sets) {
                    if (entry.getKey().equals("tagUii")) {
                        id = entry.getValue().toString();
                    } else if (entry.getKey().equals("tagRssi")) {
                        rssi = entry.getValue().toString();
                    } else if (entry.getKey().equals("tagSala")) {
                        sala = entry.getValue().toString();
                    }
                }
                
                // 4. Inserção na lista respeitando a ordem do cabeçalho
                al.add(id);
                al.add(rssi);
                al.add(sala);

                ac.add(al);
            }

            return FileXls.writeXLS(file, ac);
        } catch (Exception ex) {
            Log.i("导出异常", ex.getMessage());
            return false;
        }
    }

    public static String GetTimesyyyymmdd() {
        SimpleDateFormat formatter = new SimpleDateFormat("yyyy-MM-dd");
        Date curDate = new Date(System.currentTimeMillis());// 获取当前时间
        String dt = formatter.format(curDate);
        return dt;
    }

    public static String GetTimesddMMyy() {
        SimpleDateFormat formatter = new SimpleDateFormat("dd/MM/yy");
        Date curDate = new Date(System.currentTimeMillis());// 获取当前时间
        String dt = formatter.format(curDate);
        return dt;
    }

    public static String GetTimesyyyymmddhhmmss() {
        SimpleDateFormat formatter = new SimpleDateFormat("yyyy-MM-dd HHmmss");
        Date curDate = new Date(System.currentTimeMillis());// 获取当前时间
        String dt = formatter.format(curDate);
        return dt;
    }
}