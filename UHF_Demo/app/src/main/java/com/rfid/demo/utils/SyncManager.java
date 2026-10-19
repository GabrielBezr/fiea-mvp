package com.rfid.demo.utils;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Gerenciador de sincronização offline-first com o servidor do PC.
 * Suporta carga de bens, envio de leituras/pendências, cache offline e
 * detecção de restabelecimento de conexão.
 */
public class SyncManager {
    private static final String TAG = "SyncManager";
    private static final String PREFS_NAME = "FieaSyncPrefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String PADRAO_URL = "http://192.168.1.100:5000";

    private static SyncManager instance;
    private final Context context;
    private final SharedPreferences prefs;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final Handler mainHandler = new Handler(Looper.getMainLooper());

    private final AtomicBoolean monitorandoReconexao = new AtomicBoolean(false);
    private boolean estavaOffline = false;

    public interface Callback<T> {
        void onResult(T result);
    }

    public interface OnReconexaoListener {
        void onReconexaoDetectada(int pendenciasCount);
    }

    public static class BemEsperado {
        public String patrimonio;
        public String descricao;
        public String epc;
        public String sala;
        public boolean metalico;
        public String status = "PENDENTE"; // PENDENTE, LOCALIZADO_RFID, LOCALIZADO_QR, LOCALIZADO_MANUAL, FALTANTE

        public BemEsperado(String patrimonio, String descricao, String epc, String sala, boolean metalico) {
            this.patrimonio = patrimonio;
            this.descricao = descricao;
            this.epc = epc;
            this.sala = sala;
            this.metalico = metalico;
        }

        public JSONObject toJson() {
            try {
                JSONObject obj = new JSONObject();
                obj.put("patrimonio", patrimonio);
                obj.put("descricao", descricao);
                obj.put("epc", epc);
                obj.put("sala", sala);
                obj.put("metalico", metalico);
                obj.put("status", status);
                return obj;
            } catch (Exception e) {
                return new JSONObject();
            }
        }

        public static BemEsperado fromJson(JSONObject obj) {
            BemEsperado b = new BemEsperado(
                    obj.optString("patrimonio"),
                    obj.optString("descricao"),
                    obj.optString("epc"),
                    obj.optString("sala"),
                    obj.optBoolean("metalico")
            );
            b.status = obj.optString("status", "PENDENTE");
            return b;
        }
    }

    public static class PendenciaResolvida {
        public String patrimonio;
        public String epc;
        public String metodo; // QR, MANUAL, FALTANTE
        public String timestamp;

        public PendenciaResolvida(String patrimonio, String epc, String metodo) {
            this.patrimonio = patrimonio;
            this.epc = epc;
            this.metodo = metodo;
            this.timestamp = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US).format(new Date());
        }

        public JSONObject toJson() {
            try {
                JSONObject obj = new JSONObject();
                obj.put("patrimonio", patrimonio);
                obj.put("epc", epc);
                obj.put("metodo", metodo);
                obj.put("timestamp", timestamp);
                return obj;
            } catch (Exception e) {
                return new JSONObject();
            }
        }

        public static PendenciaResolvida fromJson(JSONObject obj) {
            PendenciaResolvida p = new PendenciaResolvida(
                    obj.optString("patrimonio"),
                    obj.optString("epc"),
                    obj.optString("metodo")
            );
            p.timestamp = obj.optString("timestamp");
            return p;
        }
    }

    private SyncManager(Context context) {
        this.context = context.getApplicationContext();
        this.prefs = this.context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    public static synchronized SyncManager getInstance(Context context) {
        if (instance == null) {
            instance = new SyncManager(context);
        }
        return instance;
    }

    public String getServerUrl() {
        return prefs.getString(KEY_SERVER_URL, PADRAO_URL);
    }

    public void setServerUrl(String url) {
        if (url != null) {
            url = url.trim();
            if (url.endsWith("/")) {
                url = url.substring(0, url.length() - 1);
            }
            prefs.edit().putString(KEY_SERVER_URL, url).apply();
        }
    }

    /**
     * Testa se o servidor no PC está acessível pela rede.
     */
    public void testarConexao(Callback<Boolean> callback) {
        executor.execute(() -> {
            boolean online = false;
            try {
                URL url = new URL(getServerUrl() + "/api/status");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(2500);
                conn.setReadTimeout(2500);
                conn.setRequestMethod("GET");
                int code = conn.getResponseCode();
                online = (code == 200);
                conn.disconnect();
            } catch (Exception ignored) {
                online = false;
            }
            final boolean res = online;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(res));
            }
        });
    }

    /**
     * Busca a lista de bens da sala.
     * Tenta primeiro a API no PC; se falhar (offline), busca no cache local do aparelho.
     */
    public void buscarBensDaSala(String sala, Callback<List<BemEsperado>> callback) {
        executor.execute(() -> {
            List<BemEsperado> bens = new ArrayList<>();
            boolean sucessoRede = false;

            try {
                String endpoint = getServerUrl() + "/api/bens" + (sala != null && !sala.isEmpty() ? "?sala=" + java.net.URLEncoder.encode(sala, "UTF-8") : "");
                URL url = new URL(endpoint);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(3000);
                conn.setRequestMethod("GET");

                if (conn.getResponseCode() == 200) {
                    BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        sb.append(line);
                    }
                    reader.close();

                    JSONArray arr = new JSONArray(sb.toString());
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject obj = arr.getJSONObject(i);
                        bens.add(BemEsperado.fromJson(obj));
                    }
                    sucessoRede = true;
                    salvarCacheBens(sala, bens);
                }
                conn.disconnect();
            } catch (Exception e) {
                Log.w(TAG, "Falha na rede ao buscar bens, recorrendo ao cache offline: " + e.getMessage());
            }

            if (!sucessoRede) {
                bens = carregarCacheBens(sala);
            }

            final List<BemEsperado> finalBens = bens;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(finalBens));
            }
        });
    }

    /**
     * Busca os nomes das salas cadastradas.
     * Tenta primeiro a API no PC; se falhar (offline), busca no cache local do aparelho.
     */
    public void buscarSalasNomes(Callback<List<String>> callback) {
        executor.execute(() -> {
            List<String> salas = new ArrayList<>();
            boolean sucessoRede = false;

            try {
                URL url = new URL(getServerUrl() + "/api/salas");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(2500);
                conn.setReadTimeout(2500);
                conn.setRequestMethod("GET");

                if (conn.getResponseCode() == 200) {
                    BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream(), StandardCharsets.UTF_8));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) {
                        sb.append(line);
                    }
                    reader.close();

                    JSONArray arr = new JSONArray(sb.toString());
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject obj = arr.getJSONObject(i);
                        String nome = obj.optString("nome", "").trim();
                        if (!nome.isEmpty() && !salas.contains(nome)) {
                            salas.add(nome);
                        }
                    }
                    sucessoRede = true;
                    salvarCacheSalas(salas);
                }
                conn.disconnect();
            } catch (Exception e) {
                Log.w(TAG, "Falha na rede ao buscar salas, recorrendo ao cache offline: " + e.getMessage());
            }

            if (!sucessoRede) {
                salas = carregarCacheSalas();
            }

            final List<String> finalSalas = salas;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(finalSalas));
            }
        });
    }

    private void salvarCacheSalas(List<String> salas) {
        try {
            File cacheFile = new File(context.getFilesDir(), "cache_salas.json");
            JSONArray arr = new JSONArray();
            for (String s : salas) {
                arr.put(s);
            }
            salvarArquivo(cacheFile, arr.toString());
        } catch (Exception e) {
            Log.w(TAG, "Erro ao salvar cache de salas: " + e.getMessage());
        }
    }

    private List<String> carregarCacheSalas() {
        List<String> salas = new ArrayList<>();
        try {
            File cacheFile = new File(context.getFilesDir(), "cache_salas.json");
            if (cacheFile.exists()) {
                String content = lerArquivo(cacheFile);
                JSONArray arr = new JSONArray(content);
                for (int i = 0; i < arr.length(); i++) {
                    String s = arr.optString(i, "").trim();
                    if (!s.isEmpty() && !salas.contains(s)) {
                        salas.add(s);
                    }
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "Erro ao carregar cache de salas: " + e.getMessage());
        }
        return salas;
    }

    /**
     * Envia um EPC lido por proximidade (baixa potência) diretamente para a tela web do PC.
     */
    public void enviarTagCapturada(String epc, Callback<Boolean> callback) {
        executor.execute(() -> {
            boolean enviou = false;
            try {
                JSONObject payload = new JSONObject();
                payload.put("epc", epc);

                URL url = new URL(getServerUrl() + "/api/tag-capturada");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(3000);
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                conn.setDoOutput(true);

                OutputStream os = conn.getOutputStream();
                os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
                os.flush();
                os.close();

                int code = conn.getResponseCode();
                enviou = (code == 200 || code == 201);
                conn.disconnect();
            } catch (Exception e) {
                Log.w(TAG, "Falha ao enviar tag capturada ao PC: " + e.getMessage());
                enviou = false;
            }

            final boolean res = enviou;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(res));
            }
        });
    }

    /**
     * Envia o pacote da varredura concluída para o PC.
     * Se falhar por estar offline, salva no arquivo de fila local para envio posterior.
     */
    public void sincronizarVarredura(Sessao sessao, Varredura varredura, List<Leitura> leituras,
                                    List<PendenciaResolvida> pendencias, Callback<Boolean> callback) {
        executor.execute(() -> {
            boolean enviou = false;
            JSONObject payload = new JSONObject();

            try {
                // Monta JSON da sessão
                if (sessao != null) {
                    JSONObject sObj = new JSONObject();
                    sObj.put("id", sessao.id);
                    sObj.put("unidade", sessao.unidade);
                    sObj.put("operador", sessao.operador);
                    sObj.put("inicio", formatarData(sessao.inicio));
                    sObj.put("fim", formatarData(sessao.fim));
                    sObj.put("duracao_s", (sessao.fim - sessao.inicio) / 1000);
                    sObj.put("n_varreduras", sessao.totalVarreduras());
                    payload.put("sessao", sObj);
                }

                // Monta JSON da varredura
                if (varredura != null) {
                    JSONObject vObj = new JSONObject();
                    vObj.put("id", varredura.id);
                    vObj.put("sessao_id", varredura.sessao.id);
                    vObj.put("sala", varredura.sala);
                    vObj.put("tecnica", varredura.tecnica);
                    vObj.put("potencia_dbm", varredura.potenciaDbm);
                    vObj.put("inicio", formatarData(varredura.inicio));
                    vObj.put("fim", formatarData(varredura.fim));
                    vObj.put("duracao_s", (varredura.fim - varredura.inicio) / 1000);
                    payload.put("varredura", vObj);
                }

                // Monta JSON das leituras
                JSONArray lArr = new JSONArray();
                if (leituras != null) {
                    for (Leitura l : leituras) {
                        JSONObject lObj = new JSONObject();
                        lObj.put("epc", l.epc);
                        lObj.put("rssi", l.rssi);
                        lObj.put("metodo", "RFID");
                        lObj.put("primeira_leitura", formatarData(l.timestamp));
                        lObj.put("ultima_leitura", formatarData(l.timestamp));
                        lArr.put(lObj);
                    }
                }
                payload.put("leituras", lArr);

                // Monta JSON das pendências
                JSONArray pArr = new JSONArray();
                if (pendencias != null) {
                    for (PendenciaResolvida p : pendencias) {
                        pArr.put(p.toJson());
                    }
                }
                payload.put("pendencias", pArr);

                // Envio HTTP POST
                URL url = new URL(getServerUrl() + "/api/sincronizar");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(4000);
                conn.setReadTimeout(4000);
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                conn.setDoOutput(true);

                OutputStream os = conn.getOutputStream();
                os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
                os.flush();
                os.close();

                int code = conn.getResponseCode();
                enviou = (code == 200 || code == 201);
                conn.disconnect();
            } catch (Exception e) {
                Log.w(TAG, "Falha ao sincronizar via Wi-Fi: " + e.getMessage());
                enviou = false;
            }

            if (!enviou) {
                // Guarda na fila offline do coletor
                enfileirarOffline(payload);
                estavaOffline = true;
            }

            final boolean res = enviou;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(res));
            }
        });
    }

    /**
     * Envia todos os pacotes acumulados na fila offline.
     */
    public void sincronizarFilaOffline(Callback<Integer> callback) {
        executor.execute(() -> {
            File filaFile = new File(context.getFilesDir(), "fila_offline.json");
            if (!filaFile.exists() || filaFile.length() == 0) {
                if (callback != null) {
                    mainHandler.post(() -> callback.onResult(0));
                }
                return;
            }

            int sucessos = 0;
            try {
                String content = lerArquivo(filaFile);
                JSONArray fila = new JSONArray(content);
                JSONArray restantes = new JSONArray();

                for (int i = 0; i < fila.length(); i++) {
                    JSONObject payload = fila.getJSONObject(i);
                    boolean enviado = false;
                    try {
                        URL url = new URL(getServerUrl() + "/api/sincronizar");
                        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                        conn.setConnectTimeout(3000);
                        conn.setReadTimeout(3000);
                        conn.setRequestMethod("POST");
                        conn.setRequestProperty("Content-Type", "application/json; charset=UTF-8");
                        conn.setDoOutput(true);

                        OutputStream os = conn.getOutputStream();
                        os.write(payload.toString().getBytes(StandardCharsets.UTF_8));
                        os.flush();
                        os.close();

                        if (conn.getResponseCode() == 200) {
                            enviado = true;
                            sucessos++;
                        }
                        conn.disconnect();
                    } catch (Exception ignored) {
                        enviado = false;
                    }

                    if (!enviado) {
                        restantes.put(payload);
                    }
                }

                if (restantes.length() > 0) {
                    salvarArquivo(filaFile, restantes.toString());
                } else {
                    filaFile.delete();
                    estavaOffline = false;
                }
            } catch (Exception e) {
                Log.e(TAG, "Erro ao processar fila offline", e);
            }

            final int total = sucessos;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(total));
            }
        });
    }

    public int totalPendenciasNaFila() {
        try {
            File filaFile = new File(context.getFilesDir(), "fila_offline.json");
            if (!filaFile.exists()) return 0;
            String content = lerArquivo(filaFile);
            return new JSONArray(content).length();
        } catch (Exception e) {
            return 0;
        }
    }

    /**
     * Inicia monitoramento de reconexão de rede.
     * Quando o Wi-Fi é restabelecido e há dados pendentes, aciona o listener.
     */
    public void monitorarReconexao(OnReconexaoListener listener) {
        if (!monitorandoReconexao.compareAndSet(false, true)) {
            return;
        }

        ConnectivityManager cm = (ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return;

        NetworkRequest request = new NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .build();

        cm.registerNetworkCallback(request, new ConnectivityManager.NetworkCallback() {
            @Override
            public void onAvailable(Network network) {
                // Checa se o servidor local responde
                testarConexao(online -> {
                    if (online) {
                        int pendentes = totalPendenciasNaFila();
                        if (pendentes > 0 || estavaOffline) {
                            estavaOffline = false;
                            mainHandler.post(() -> listener.onReconexaoDetectada(pendentes));
                        }
                    }
                });
            }

            @Override
            public void onLost(Network network) {
                estavaOffline = true;
            }
        });
    }

    // ---------------------------------------------------------------- Funções Internas de Cache

    private synchronized void enfileirarOffline(JSONObject payload) {
        try {
            File filaFile = new File(context.getFilesDir(), "fila_offline.json");
            JSONArray fila;
            if (filaFile.exists() && filaFile.length() > 0) {
                fila = new JSONArray(lerArquivo(filaFile));
            } else {
                fila = new JSONArray();
            }
            fila.put(payload);
            salvarArquivo(filaFile, fila.toString());
            Log.i(TAG, "Payload salvo na fila offline. Total na fila: " + fila.length());
        } catch (Exception e) {
            Log.e(TAG, "Erro ao enfileirar payload offline", e);
        }
    }

    private void salvarCacheBens(String sala, List<BemEsperado> bens) {
        try {
            String nomeArquivo = "cache_bens_" + (sala != null && !sala.isEmpty() ? sala.replaceAll("[^a-zA-Z0-9]", "_") : "todas") + ".json";
            File cacheFile = new File(context.getFilesDir(), nomeArquivo);
            JSONArray arr = new JSONArray();
            for (BemEsperado b : bens) {
                arr.put(b.toJson());
            }
            salvarArquivo(cacheFile, arr.toString());
        } catch (Exception e) {
            Log.w(TAG, "Erro ao salvar cache de bens: " + e.getMessage());
        }
    }

    private List<BemEsperado> carregarCacheBens(String sala) {
        List<BemEsperado> bens = new ArrayList<>();
        try {
            String nomeArquivo = "cache_bens_" + (sala != null && !sala.isEmpty() ? sala.replaceAll("[^a-zA-Z0-9]", "_") : "todas") + ".json";
            File cacheFile = new File(context.getFilesDir(), nomeArquivo);
            if (cacheFile.exists()) {
                String content = lerArquivo(cacheFile);
                JSONArray arr = new JSONArray(content);
                for (int i = 0; i < arr.length(); i++) {
                    bens.add(BemEsperado.fromJson(arr.getJSONObject(i)));
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "Erro ao ler cache de bens: " + e.getMessage());
        }
        return bens;
    }

    private String lerArquivo(File file) throws Exception {
        FileInputStream fis = new FileInputStream(file);
        byte[] data = new byte[(int) file.length()];
        fis.read(data);
        fis.close();
        return new String(data, StandardCharsets.UTF_8);
    }

    private void salvarArquivo(File file, String content) throws Exception {
        FileOutputStream fos = new FileOutputStream(file, false);
        fos.write(content.getBytes(StandardCharsets.UTF_8));
        fos.close();
    }

    private String formatarData(long timestamp) {
        return new SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US).format(new Date(timestamp));
    }
}
