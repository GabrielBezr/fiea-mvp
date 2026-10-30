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
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Iterator;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
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
    private final AtomicBoolean sincronizandoFila = new AtomicBoolean(false);
    private boolean estavaOffline = false;

    public interface Callback<T> {
        void onResult(T result);
    }

    public interface OnReconexaoListener {
        void onReconexaoDetectada(int pendenciasCount);
    }

    /** Sala cadastrada no PC; a unidade separa salas de mesmo nome em prédios diferentes. */
    public static class SalaCadastrada {
        public final String nome;
        public final String unidade;

        public SalaCadastrada(String nome, String unidade) {
            this.nome = nome;
            this.unidade = unidade != null ? unidade : "";
        }
    }

    public static class BemEsperado {
        public String patrimonio;
        public String descricao;
        public String epc;
        public String sala;
        public boolean metalico;
        public String status = "PENDENTE"; // PENDENTE, LOCALIZADO_RFID, LOCALIZADO_QR, LOCALIZADO_MANUAL, FALTANTE, FORA_DO_LOCAL
        public String outrasSalas = null;

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
                if (outrasSalas != null) {
                    obj.put("outrasSalas", outrasSalas);
                }
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
            if (obj.has("outrasSalas")) {
                b.outrasSalas = obj.optString("outrasSalas");
            }
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
                int filaCount = totalPendenciasNaFila();
                URL url = new URL(getServerUrl() + "/api/status?fila=" + filaCount);
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
     * Busca a lista de bens da sala, na unidade da sessão.
     * Tenta primeiro a API no PC; se falhar (offline), busca no cache local do aparelho.
     */
    public void buscarBensDaSala(String sala, String unidade, Callback<List<BemEsperado>> callback) {
        executor.execute(() -> {
            List<BemEsperado> bens = new ArrayList<>();
            boolean sucessoRede = false;

            try {
                String endpoint = getServerUrl() + "/api/bens";
                if (sala != null && !sala.isEmpty()) {
                    endpoint += "?sala=" + java.net.URLEncoder.encode(sala, "UTF-8");
                    if (unidade != null && !unidade.isEmpty()) {
                        endpoint += "&unidade=" + java.net.URLEncoder.encode(unidade, "UTF-8");
                    }
                }
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
                    salvarCacheBens(sala, unidade, bens);
                }
                conn.disconnect();
            } catch (Exception e) {
                Log.w(TAG, "Falha na rede ao buscar bens, recorrendo ao cache offline: " + e.getMessage());
            }

            if (!sucessoRede) {
                bens = carregarCacheBens(sala, unidade);
            }

            final List<BemEsperado> finalBens = bens;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(finalBens));
            }
        });
    }

    /**
     * Busca as salas cadastradas, com a unidade de cada uma.
     * Tenta primeiro a API no PC; se falhar (offline), busca no cache local do aparelho.
     */
    public void buscarSalas(Callback<List<SalaCadastrada>> callback) {
        executor.execute(() -> {
            List<SalaCadastrada> salas = new ArrayList<>();
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
                        adicionarSala(salas, obj.optString("nome", ""), obj.optString("unidade", ""));
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

            final List<SalaCadastrada> finalSalas = salas;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(finalSalas));
            }
        });
    }

    /** Acrescenta a sala se ainda não houver uma com o mesmo nome na mesma unidade. */
    private static void adicionarSala(List<SalaCadastrada> salas, String nome, String unidade) {
        nome = nome != null ? nome.trim() : "";
        unidade = unidade != null ? unidade.trim() : "";
        if (nome.isEmpty()) {
            return;
        }
        for (SalaCadastrada s : salas) {
            if (s.nome.equals(nome) && s.unidade.equals(unidade)) {
                return;
            }
        }
        salas.add(new SalaCadastrada(nome, unidade));
    }

    private void salvarCacheSalas(List<SalaCadastrada> salas) {
        try {
            File cacheFile = new File(context.getFilesDir(), "cache_salas.json");
            JSONArray arr = new JSONArray();
            for (SalaCadastrada s : salas) {
                JSONObject obj = new JSONObject();
                obj.put("nome", s.nome);
                obj.put("unidade", s.unidade);
                arr.put(obj);
            }
            salvarArquivo(cacheFile, arr.toString());
        } catch (Exception e) {
            Log.w(TAG, "Erro ao salvar cache de salas: " + e.getMessage());
        }
    }

    private List<SalaCadastrada> carregarCacheSalas() {
        List<SalaCadastrada> salas = new ArrayList<>();
        try {
            File cacheFile = new File(context.getFilesDir(), "cache_salas.json");
            if (cacheFile.exists()) {
                String content = lerArquivo(cacheFile);
                JSONArray arr = new JSONArray(content);
                for (int i = 0; i < arr.length(); i++) {
                    JSONObject obj = arr.optJSONObject(i);
                    if (obj != null) {
                        adicionarSala(salas, obj.optString("nome", ""), obj.optString("unidade", ""));
                    } else {
                        // Cache das versões anteriores: só o nome, sem unidade.
                        adicionarSala(salas, arr.optString(i, ""), "");
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
        if (!sincronizandoFila.compareAndSet(false, true)) {
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(0));
            }
            return;
        }
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
            } finally {
                sincronizandoFila.set(false);
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
            if (!filaFile.exists() || filaFile.length() == 0) return 0;
            String content = lerArquivo(filaFile);
            return new JSONArray(content).length();
        } catch (Exception e) {
            return 0;
        }
    }

    /**
     * Retorna o total de sessões distintas acumuladas na fila offline.
     */
    public int totalSessoesNaFila() {
        try {
            File filaFile = new File(context.getFilesDir(), "fila_offline.json");
            if (!filaFile.exists() || filaFile.length() == 0) return 0;
            String content = lerArquivo(filaFile);
            JSONArray fila = new JSONArray(content);
            Set<String> sessoes = new HashSet<>();
            for (int i = 0; i < fila.length(); i++) {
                JSONObject payload = fila.optJSONObject(i);
                if (payload != null && payload.has("sessao")) {
                    JSONObject sObj = payload.optJSONObject("sessao");
                    if (sObj != null) {
                        String sId = sObj.optString("id");
                        if (!sId.isEmpty()) {
                            sessoes.add(sId);
                        }
                    }
                }
            }
            return sessoes.isEmpty() ? (fila.length() > 0 ? 1 : 0) : sessoes.size();
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

    /**
     * Arquivo do cache de bens. Com a unidade no nome, salas homônimas de unidades
     * diferentes não se sobrescrevem; sem unidade, é o nome que as versões anteriores usavam.
     */
    private static String arquivoCacheBens(String sala, String unidade) {
        String chaveSala = sala != null && !sala.isEmpty() ? sala.replaceAll("[^a-zA-Z0-9]", "_") : "todas";
        if (unidade == null || unidade.isEmpty()) {
            return "cache_bens_" + chaveSala + ".json";
        }
        return "cache_bens_" + unidade.replaceAll("[^a-zA-Z0-9]", "_") + "__" + chaveSala + ".json";
    }

    private void salvarCacheBens(String sala, String unidade, List<BemEsperado> bens) {
        try {
            File cacheFile = new File(context.getFilesDir(), arquivoCacheBens(sala, unidade));
            JSONArray arr = new JSONArray();
            for (BemEsperado b : bens) {
                arr.put(b.toJson());
            }
            salvarArquivo(cacheFile, arr.toString());
        } catch (Exception e) {
            Log.w(TAG, "Erro ao salvar cache de bens: " + e.getMessage());
        }
    }

    private List<BemEsperado> carregarCacheBens(String sala, String unidade) {
        List<BemEsperado> bens = new ArrayList<>();
        try {
            File cacheFile = new File(context.getFilesDir(), arquivoCacheBens(sala, unidade));
            // Cache gravado antes da unidade entrar na chave: serve até a próxima ida ao PC.
            if (!cacheFile.exists()) {
                cacheFile = new File(context.getFilesDir(), arquivoCacheBens(sala, null));
            }
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

    public static class ResumoBase {
        public final int totalSalas;
        public final int totalBens;
        public final int totalRfid;
        public final boolean sucessoRede;

        public ResumoBase(int totalSalas, int totalBens, int totalRfid, boolean sucessoRede) {
            this.totalSalas = totalSalas;
            this.totalBens = totalBens;
            this.totalRfid = totalRfid;
            this.sucessoRede = sucessoRede;
        }
    }

    public void registrarUltimaVarredura(String sala, int tagsLidas, long timestamp) {
        prefs.edit()
                .putString("ultima_varredura_sala", sala != null ? sala : "")
                .putInt("ultima_varredura_tags", tagsLidas)
                .putLong("ultima_varredura_tempo", timestamp)
                .apply();
    }

    public String getUltimaVarreduraTexto(Context ctx) {
        String sala = prefs.getString("ultima_varredura_sala", "");
        if (sala.isEmpty()) {
            return ctx.getString(com.rfid.demo.R.string.painelSemVarredura);
        }
        int tags = prefs.getInt("ultima_varredura_tags", 0);
        long tempo = prefs.getLong("ultima_varredura_tempo", 0);
        String hora = new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date(tempo));
        return ctx.getString(com.rfid.demo.R.string.painelUltimaVarredura, sala, tags, hora);
    }

    public static class ResumoPanorama {
        public final List<String> unidades;
        public final Map<String, ResumoBase> dadosPorUnidade;
        public final boolean sucessoRede;

        public ResumoPanorama(List<String> unidades, Map<String, ResumoBase> dadosPorUnidade, boolean sucessoRede) {
            this.unidades = unidades != null ? unidades : new ArrayList<String>();
            this.dadosPorUnidade = dadosPorUnidade != null ? dadosPorUnidade : new HashMap<String, ResumoBase>();
            this.sucessoRede = sucessoRede;
        }

        public ResumoBase getResumo(String unidade) {
            if (unidade == null || unidade.isEmpty() || !dadosPorUnidade.containsKey(unidade)) {
                if (dadosPorUnidade.containsKey("")) {
                    return dadosPorUnidade.get("");
                }
                return new ResumoBase(0, 0, 0, sucessoRede);
            }
            return dadosPorUnidade.get(unidade);
        }
    }

    public ResumoBase getResumoBaseCache() {
        return getResumoPanoramaCache().getResumo("");
    }

    public ResumoPanorama getResumoPanoramaCache() {
        try {
            File f = new File(context.getFilesDir(), "cache_panorama.json");
            if (f.exists()) {
                String content = lerArquivo(f);
                JSONObject root = new JSONObject(content);
                List<String> unidades = new ArrayList<>();
                JSONArray arrU = root.optJSONArray("unidades");
                if (arrU != null) {
                    for (int i = 0; i < arrU.length(); i++) {
                        unidades.add(arrU.getString(i));
                    }
                }
                Map<String, ResumoBase> mapa = new HashMap<>();
                JSONObject dados = root.optJSONObject("dados");
                if (dados != null) {
                    Iterator<String> keys = dados.keys();
                    while (keys.hasNext()) {
                        String k = keys.next();
                        JSONObject d = dados.getJSONObject(k);
                        mapa.put(k, new ResumoBase(
                                d.optInt("salas", 0),
                                d.optInt("bens", 0),
                                d.optInt("rfid", 0),
                                false
                        ));
                    }
                }
                return new ResumoPanorama(unidades, mapa, false);
            }
        } catch (Exception e) {
            Log.w(TAG, "Erro ao ler cache_panorama.json: " + e.getMessage());
        }

        int salas = prefs.getInt("cache_total_salas", 0);
        int bens = prefs.getInt("cache_total_bens", 0);
        int rfid = prefs.getInt("cache_total_rfid", 0);
        Map<String, ResumoBase> mapaFallback = new HashMap<>();
        mapaFallback.put("", new ResumoBase(salas, bens, rfid, false));
        return new ResumoPanorama(new ArrayList<String>(), mapaFallback, false);
    }

    public void carregarResumoPanorama(Callback<ResumoPanorama> callback) {
        executor.execute(() -> {
            boolean sucessoRede = false;
            Set<String> setUnidades = new TreeSet<>(String.CASE_INSENSITIVE_ORDER);
            Map<String, Set<String>> salasPorUnidade = new HashMap<>();
            salasPorUnidade.put("", new HashSet<>());

            Map<String, Integer> bensPorUnidade = new HashMap<>();
            bensPorUnidade.put("", 0);

            Map<String, Integer> rfidPorUnidade = new HashMap<>();
            rfidPorUnidade.put("", 0);

            try {
                URL urlSalas = new URL(getServerUrl() + "/api/salas");
                HttpURLConnection connSalas = (HttpURLConnection) urlSalas.openConnection();
                connSalas.setConnectTimeout(3000);
                connSalas.setReadTimeout(3000);
                connSalas.setRequestMethod("GET");
                if (connSalas.getResponseCode() == 200) {
                    BufferedReader reader = new BufferedReader(new InputStreamReader(connSalas.getInputStream(), StandardCharsets.UTF_8));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    JSONArray arr = new JSONArray(sb.toString());
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject s = arr.getJSONObject(i);
                        String nome = s.optString("nome", "").trim();
                        String unidade = s.optString("unidade", "").trim();
                        if (!nome.isEmpty()) {
                            salasPorUnidade.get("").add(nome);
                            if (!unidade.isEmpty()) {
                                setUnidades.add(unidade);
                                if (!salasPorUnidade.containsKey(unidade)) {
                                    salasPorUnidade.put(unidade, new HashSet<>());
                                }
                                salasPorUnidade.get(unidade).add(nome);
                            }
                        }
                    }
                    sucessoRede = true;
                }
                connSalas.disconnect();
            } catch (Exception ignored) {
            }

            try {
                URL urlBens = new URL(getServerUrl() + "/api/bens");
                HttpURLConnection connBens = (HttpURLConnection) urlBens.openConnection();
                connBens.setConnectTimeout(3000);
                connBens.setReadTimeout(3000);
                connBens.setRequestMethod("GET");
                if (connBens.getResponseCode() == 200) {
                    BufferedReader reader = new BufferedReader(new InputStreamReader(connBens.getInputStream(), StandardCharsets.UTF_8));
                    StringBuilder sb = new StringBuilder();
                    String line;
                    while ((line = reader.readLine()) != null) sb.append(line);
                    reader.close();
                    JSONArray arr = new JSONArray(sb.toString());
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject b = arr.getJSONObject(i);
                        String epc = b.optString("epc", "").trim();
                        String unidade = b.optString("unidade", "").trim();
                        boolean temRfid = !epc.isEmpty();

                        bensPorUnidade.put("", bensPorUnidade.get("") + 1);
                        if (temRfid) {
                            rfidPorUnidade.put("", rfidPorUnidade.get("") + 1);
                        }

                        if (!unidade.isEmpty()) {
                            setUnidades.add(unidade);
                            int bAtual = bensPorUnidade.containsKey(unidade) ? bensPorUnidade.get(unidade) : 0;
                            bensPorUnidade.put(unidade, bAtual + 1);
                            if (temRfid) {
                                int rAtual = rfidPorUnidade.containsKey(unidade) ? rfidPorUnidade.get(unidade) : 0;
                                rfidPorUnidade.put(unidade, rAtual + 1);
                            }
                        }
                    }
                    sucessoRede = true;
                }
                connBens.disconnect();
            } catch (Exception ignored) {
            }

            ResumoPanorama panorama;
            if (sucessoRede) {
                List<String> listaUnidades = new ArrayList<>(setUnidades);
                Map<String, ResumoBase> dadosMapa = new HashMap<>();

                int salasG = salasPorUnidade.get("").size();
                int bensG = bensPorUnidade.get("");
                int rfidG = rfidPorUnidade.get("");
                dadosMapa.put("", new ResumoBase(salasG, bensG, rfidG, true));

                for (String u : listaUnidades) {
                    int salasU = salasPorUnidade.containsKey(u) ? salasPorUnidade.get(u).size() : 0;
                    int bensU = bensPorUnidade.containsKey(u) ? bensPorUnidade.get(u) : 0;
                    int rfidU = rfidPorUnidade.containsKey(u) ? rfidPorUnidade.get(u) : 0;
                    dadosMapa.put(u, new ResumoBase(salasU, bensU, rfidU, true));
                }

                try {
                    JSONObject root = new JSONObject();
                    JSONArray arrU = new JSONArray();
                    for (String u : listaUnidades) arrU.put(u);
                    root.put("unidades", arrU);

                    JSONObject dadosObj = new JSONObject();
                    for (Map.Entry<String, ResumoBase> entry : dadosMapa.entrySet()) {
                        JSONObject d = new JSONObject();
                        d.put("salas", entry.getValue().totalSalas);
                        d.put("bens", entry.getValue().totalBens);
                        d.put("rfid", entry.getValue().totalRfid);
                        dadosObj.put(entry.getKey(), d);
                    }
                    root.put("dados", dadosObj);

                    File f = new File(context.getFilesDir(), "cache_panorama.json");
                    salvarArquivo(f, root.toString());

                    prefs.edit()
                            .putInt("cache_total_salas", salasG)
                            .putInt("cache_total_bens", bensG)
                            .putInt("cache_total_rfid", rfidG)
                            .apply();
                } catch (Exception e) {
                    Log.w(TAG, "Erro ao salvar cache_panorama.json: " + e.getMessage());
                }

                panorama = new ResumoPanorama(listaUnidades, dadosMapa, true);
            } else {
                panorama = getResumoPanoramaCache();
            }

            final ResumoPanorama resultado = panorama;
            if (callback != null) {
                mainHandler.post(() -> callback.onResult(resultado));
            }
        });
    }

    public void carregarResumoBase(Callback<ResumoBase> callback) {
        carregarResumoPanorama(panorama -> {
            if (callback != null) {
                callback.onResult(panorama.getResumo(""));
            }
        });
    }
}
