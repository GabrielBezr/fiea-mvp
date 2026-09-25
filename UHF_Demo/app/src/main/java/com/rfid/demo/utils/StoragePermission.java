package com.rfid.demo.utils;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.Settings;

import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;

/**
 * Acesso de escrita à pasta pública onde os arquivos exportados são gravados.
 *
 * O app grava em /sdcard/XLSFile/, fora do seu diretório privado. A partir do
 * Android 11 isso exige "acesso a todos os arquivos" (MANAGE_EXTERNAL_STORAGE),
 * que só o usuário concede, pela tela de configurações. Abaixo disso basta o
 * WRITE_EXTERNAL_STORAGE concedido em tempo de execução.
 */
public class StoragePermission {

    public static final int REQUEST_CODE = 1001;

    /** true quando a concessão é feita na tela de configurações, e não por diálogo. */
    public static boolean usaTelaDeConfiguracoes() {
        return Build.VERSION.SDK_INT >= Build.VERSION_CODES.R;
    }

    public static boolean concedida(Context context) {
        if (usaTelaDeConfiguracoes()) {
            return Environment.isExternalStorageManager();
        }
        return ContextCompat.checkSelfPermission(context, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                == PackageManager.PERMISSION_GRANTED;
    }

    /**
     * Pede a permissão. No Android 11+ abre a tela de configurações (o retorno
     * chega em onResume); abaixo disso dispara o diálogo padrão, cuja resposta
     * chega em onRequestPermissionsResult com REQUEST_CODE.
     */
    public static void solicitar(Activity activity) {
        if (!usaTelaDeConfiguracoes()) {
            ActivityCompat.requestPermissions(activity,
                    new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQUEST_CODE);
            return;
        }
        // A tela específica do app pode não existir em algumas ROMs; cai na lista geral.
        try {
            Intent intent = new Intent(Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION);
            intent.setData(Uri.parse("package:" + activity.getPackageName()));
            activity.startActivity(intent);
        } catch (Exception ex) {
            try {
                activity.startActivity(new Intent(Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION));
            } catch (Exception ignored) {
            }
        }
    }
}
