package com.rfid.demo;

import android.app.Activity;
import android.app.TabActivity;
import android.content.Intent;
import android.content.res.ColorStateList;
import android.os.Build;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.app.AlertDialog;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.TabHost;
import android.widget.TabWidget;
import android.widget.TextView;
import android.widget.Toast;

import androidx.core.content.ContextCompat;
import androidx.core.view.ViewCompat;

import com.google.android.material.bottomnavigation.BottomNavigationView;
import com.rfid.demo.utils.Dialogos;
import com.ubx.usdk.RFIDSDKManager;

public class MainActivity extends TabActivity   {

	// Índices das abas no TabHost. As quatro primeiras aparecem na navegação inferior;
	// da ABA_LOCALIZAR em diante são as ferramentas do demo, só pela área de desenvolvedor.
	public static final int ABA_PAINEL = 0;
	public static final int ABA_INVENTARIO = 1;
	public static final int ABA_PENDENCIAS = 2;
	public static final int ABA_ETIQUETAGEM = 3;
	public static final int ABA_LOCALIZAR = 4;

	private static final int CONEXAO_VERIFICANDO = 0;
	private static final int CONEXAO_ONLINE = 1;
	private static final int CONEXAO_OFFLINE = 2;

	public static TabHost myTabHost;
	private int abaAnterior = ABA_INVENTARIO;
	private View layoutConexaoServidor, btConfigServidor;
	private TextView tvStatusConexao;
	private ImageView ivStatusConexao;
	private View barraSuperior, btSairDev, divisorNavegacao;
	private ImageView ivLogo;
	private TextView tvTitulo;
	private BottomNavigationView navegacao;
	private final Handler handlerConexao = new Handler(Looper.getMainLooper());
	private Runnable runnableVerificarConexao;

	@Override
	protected void onCreate(Bundle savedInstanceState) {
		super.onCreate(savedInstanceState);
		requestWindowFeature(Window.FEATURE_NO_TITLE);
		setContentView(R.layout.activity_main);
		myTabHost = getTabHost();
		Intent intentPainel = new Intent(this, PainelActivity.class);
		Intent intent0 = new Intent(this, ScanActivity.class);
		Intent intent1 = new Intent(this,ReadWriteActivity.class);
		Intent intent2 = new Intent(this, SettingActivity.class);
		Intent intent3 = new Intent(this,MaskActivity.class);
		Intent intent4 = new Intent(this, FindingActivity.class);
		Intent intent5 = new Intent(this, EtiquetagemActivity.class);
		Intent intent6 = new Intent(this, TagsSalaActivity.class);

		TabHost.TabSpec tabSpecPainel = myTabHost.newTabSpec(getString(R.string.tab_painel)).setIndicator(getString(R.string.tab_painel)).setContent(intentPainel);
		TabHost.TabSpec tabSpec0 = myTabHost.newTabSpec(getString(R.string.tab_scan)).setIndicator(getString(R.string.tab_scan)).setContent(intent0);
		TabHost.TabSpec tabSpec1 = myTabHost.newTabSpec(getString(R.string.tab_rw)).setIndicator(getString(R.string.tab_rw)).setContent(intent1);
		TabHost.TabSpec tabSpec2 = myTabHost.newTabSpec(getString(R.string.tab_param)).setIndicator(getString(R.string.tab_param)).setContent(intent2);
		TabHost.TabSpec tabSpec3 = myTabHost.newTabSpec(getString(R.string.tab_mask)).setIndicator(getString(R.string.tab_mask)).setContent(intent3);
		TabHost.TabSpec tabSpec4 = myTabHost.newTabSpec(getString(R.string.finding)).setIndicator(getString(R.string.finding)).setContent(intent4);
		TabHost.TabSpec tabSpec5 = myTabHost.newTabSpec(getString(R.string.tab_etiquetagem)).setIndicator(getString(R.string.tab_etiquetagem)).setContent(intent5);
		TabHost.TabSpec tabSpec6 = myTabHost.newTabSpec(getString(R.string.tab_pendencias)).setIndicator(getString(R.string.tab_pendencias)).setContent(intent6);

		myTabHost.addTab(tabSpecPainel);
		myTabHost.addTab(tabSpec0);
		myTabHost.addTab(tabSpec6);
		myTabHost.addTab(tabSpec5);
		myTabHost.addTab(tabSpec4);
		myTabHost.addTab(tabSpec1);
		myTabHost.addTab(tabSpec3);
		myTabHost.addTab(tabSpec2);

		myTabHost.setCurrentTab(ABA_PAINEL);

		myTabHost.setOnTabChangedListener(new TabHost.OnTabChangeListener() {
			@Override
			public void onTabChanged(String s) {
				Activity current = getCurrentActivity();
				if (current instanceof ScanActivity) {
					((ScanActivity) current).registrarCallback();
				}
				// Ao sair de Pendências, a aba volta para a lista de salas: na volta o
				// operador escolhe a próxima sala, em vez de cair na última que resolveu.
				int aba = myTabHost.getCurrentTab();
				if (abaAnterior == ABA_PENDENCIAS && aba != ABA_PENDENCIAS) {
					Activity pendencias = getLocalActivityManager().getActivity(getString(R.string.tab_pendencias));
					if (pendencias instanceof TagsSalaActivity) {
						((TagsSalaActivity) pendencias).voltarParaSalas();
					}
				}
				abaAnterior = aba;
				atualizarCasca(aba);
			}
		});

		// Sala desconhecida na varredura: o PC recusa a sincronização (409), então o
		// app avisa o operador em vez de perder o dado numa fila muda.
		com.rfid.demo.utils.SyncManager.getInstance(this).setOnSalaDesconhecidaListener((sala, unidade, conhecidas) -> {
			StringBuilder sb = new StringBuilder();
			if (conhecidas == null || conhecidas.isEmpty()) {
				sb.append("Nenhuma sala cadastrada no servidor ainda.");
			} else {
				sb.append("Salas cadastradas: ");
				for (int i = 0; i < conhecidas.size(); i++) {
					if (i > 0) sb.append(", ");
					sb.append(conhecidas.get(i).nome);
				}
			}
			new AlertDialog.Builder(MainActivity.this)
					.setTitle(R.string.salaNaoCadastradaTitulo)
					.setMessage(getString(R.string.salaNaoCadastradaMsg, sala, sb.toString()))
					.setPositiveButton(R.string.salaNaoCadastradaCriar, (dialog, which) -> {
						com.rfid.demo.utils.SyncManager.getInstance(MainActivity.this)
								.criarSala(sala, unidade, ok -> {
									if (ok) {
										Toast.makeText(MainActivity.this, R.string.salaCriada, Toast.LENGTH_LONG).show();
										com.rfid.demo.utils.SyncManager.getInstance(MainActivity.this).sincronizarFilaOffline(null);
									} else {
										Toast.makeText(MainActivity.this, R.string.salaNaoCriada, Toast.LENGTH_LONG).show();
									}
								});
					})
					.setNegativeButton(R.string.salaNaoCadastradaEspera, null)
					.setCancelable(true)
					.show();
		});

		// Monitor de Reconexão Wi-Fi (Modo Offline com Alerta)
		com.rfid.demo.utils.SyncManager.getInstance(this).monitorarReconexao(new com.rfid.demo.utils.SyncManager.OnReconexaoListener() {
			@Override
			public void onReconexaoDetectada(int pendenciasCount) {
				new android.app.AlertDialog.Builder(MainActivity.this)
						.setTitle(R.string.msgReconexaoTitulo)
						.setMessage(R.string.msgReconexaoTexto)
						.setPositiveButton(R.string.btSincronizarAgora, (dialog, which) -> {
							com.rfid.demo.utils.SyncManager.getInstance(MainActivity.this).sincronizarFilaOffline(sucessos -> {
								android.widget.Toast.makeText(MainActivity.this, getString(R.string.msgFilaSincronizada, sucessos), android.widget.Toast.LENGTH_LONG).show();
							});
						})
						.setNegativeButton(R.string.btResolverDepois, null)
						.show();
			}
		});

//		changeTabBottomBg();



		// Barra de status na cor da barra superior, com ícones claros.
		setStatusBarColor(this, ContextCompat.getColor(this, R.color.fiea_azul));
		inicializarBarraConexao();
		inicializarNavegacao();
		atualizarCasca(myTabHost.getCurrentTab());
	}

	private void inicializarNavegacao() {
		barraSuperior = findViewById(R.id.barra_superior);
		ivLogo = findViewById(R.id.iv_logo);
		tvTitulo = findViewById(R.id.tv_titulo);
		btSairDev = findViewById(R.id.bt_sair_dev);
		divisorNavegacao = findViewById(R.id.divisor_navegacao);
		navegacao = findViewById(R.id.navegacao);

		navegacao.setOnItemSelectedListener(item -> {
			myTabHost.setCurrentTab(abaDoItem(item.getItemId()));
			return true;
		});

		// Ao tocar novamente na aba já aberta (ex: Pendências), volta à tela inicial dela
		navegacao.setOnItemReselectedListener(item -> {
			int aba = abaDoItem(item.getItemId());
			if (aba == ABA_PENDENCIAS) {
				Activity pendencias = getCurrentActivity();
				if (!(pendencias instanceof TagsSalaActivity)) {
					pendencias = getLocalActivityManager().getActivity(getString(R.string.tab_pendencias));
				}
				if (pendencias instanceof TagsSalaActivity) {
					((TagsSalaActivity) pendencias).voltarParaSalas();
				}
			}
		});

		// Escondida de propósito: o operador não precisa das ferramentas do demo.
		View areaTitulo = findViewById(R.id.area_titulo);
		areaTitulo.setOnLongClickListener(v -> {
			abrirMenuDesenvolvedor();
			return true;
		});
		// Já no modo desenvolvedor, um toque no título troca de ferramenta.
		areaTitulo.setOnClickListener(v -> {
			if (myTabHost.getCurrentTab() >= ABA_LOCALIZAR) {
				abrirMenuDesenvolvedor();
			}
		});
		btSairDev.setOnClickListener(v -> myTabHost.setCurrentTab(ABA_PAINEL));
	}

	private static int abaDoItem(int itemId) {
		if (itemId == R.id.nav_painel) return ABA_PAINEL;
		if (itemId == R.id.nav_pendencias) return ABA_PENDENCIAS;
		if (itemId == R.id.nav_etiquetagem) return ABA_ETIQUETAGEM;
		return ABA_INVENTARIO;
	}

	private static int itemDaAba(int aba) {
		if (aba == ABA_PAINEL) return R.id.nav_painel;
		if (aba == ABA_PENDENCIAS) return R.id.nav_pendencias;
		if (aba == ABA_ETIQUETAGEM) return R.id.nav_etiquetagem;
		return R.id.nav_inventario;
	}

	/**
	 * Ajusta a barra superior e a navegação à aba atual: azul nas abas do operador,
	 * escura e sem navegação inferior nas ferramentas de desenvolvedor.
	 */
	private void atualizarCasca(int aba) {
		boolean dev = aba >= ABA_LOCALIZAR;
		int corBarra = ContextCompat.getColor(this, dev ? R.color.modo_dev : R.color.fiea_azul);
		barraSuperior.setBackgroundColor(corBarra);
		getWindow().setStatusBarColor(corBarra);
		ivLogo.setVisibility(dev ? View.GONE : View.VISIBLE);
		btSairDev.setVisibility(dev ? View.VISIBLE : View.GONE);
		if (dev) {
			String[] nomes = getResources().getStringArray(R.array.devNomesCurtos);
			tvTitulo.setText(getString(R.string.devTituloBarra, nomes[aba - ABA_LOCALIZAR]));
		} else {
			tvTitulo.setText(R.string.app_titulo);
		}
		navegacao.setVisibility(dev ? View.GONE : View.VISIBLE);
		divisorNavegacao.setVisibility(dev ? View.GONE : View.VISIBLE);
		// Acompanha também as trocas feitas por código (ScanActivity abre Pendências).
		if (!dev && navegacao.getSelectedItemId() != itemDaAba(aba)) {
			navegacao.setSelectedItemId(itemDaAba(aba));
		}
	}

	private void abrirMenuDesenvolvedor() {
		new AlertDialog.Builder(this)
				.setTitle(R.string.devTitulo)
				.setItems(R.array.devFerramentas, (dialog, which) -> myTabHost.setCurrentTab(ABA_LOCALIZAR + which))
				.setNegativeButton(R.string.btCancelar, null)
				.show();
	}

	private void inicializarBarraConexao() {
		layoutConexaoServidor = findViewById(R.id.layout_conexao_servidor);
		tvStatusConexao = findViewById(R.id.tv_status_conexao);
		ivStatusConexao = findViewById(R.id.iv_status_conexao);
		btConfigServidor = findViewById(R.id.bt_config_servidor);

		View.OnClickListener abrirConfig = v -> abrirDialogoConfigIp();
		if (btConfigServidor != null) btConfigServidor.setOnClickListener(abrirConfig);
		if (layoutConexaoServidor != null) layoutConexaoServidor.setOnClickListener(abrirConfig);

		runnableVerificarConexao = new Runnable() {
			@Override
			public void run() {
				verificarStatusServidor();
				handlerConexao.postDelayed(this, 8000);
			}
		};
	}

	private void verificarStatusServidor() {
		com.rfid.demo.utils.SyncManager sm = com.rfid.demo.utils.SyncManager.getInstance(this);
		sm.testarConexao(online -> {
			if (layoutConexaoServidor == null || tvStatusConexao == null) return;
			// A fila aparece também offline: é quando o operador mais precisa saber dela.
			int pendentes = sm.totalPendenciasNaFila();
			mostrarConexao(online ? CONEXAO_ONLINE : CONEXAO_OFFLINE, pendentes);

			// Se o servidor estiver online e houver pendências na fila offline, sincroniza automaticamente
			if (online && pendentes > 0) {
				sm.sincronizarFilaOffline(sucessos -> {
					if (sucessos > 0) {
						int restante = sm.totalPendenciasNaFila();
						mostrarConexao(CONEXAO_ONLINE, restante);
						Activity cur = getCurrentActivity();
						if (cur instanceof PainelActivity) {
							((PainelActivity) cur).atualizarFilaOffline();
						}
					}
				});
			}
		});
	}

	/** Pinta o chip de conexão da barra superior; naFila > 0 acrescenta a contagem. */
	private void mostrarConexao(int estado, int naFila) {
		int fundo, cor, icone, rotulo;
		if (estado == CONEXAO_ONLINE) {
			fundo = R.color.sucesso_fundo;
			cor = R.color.sucesso_texto;
			icone = R.drawable.ic_wifi;
			rotulo = R.string.conexaoOnline;
		} else if (estado == CONEXAO_OFFLINE) {
			fundo = R.color.erro_fundo;
			cor = R.color.erro_texto;
			icone = R.drawable.ic_wifi_off;
			rotulo = R.string.conexaoOffline;
		} else {
			fundo = R.color.alerta_fundo;
			cor = R.color.alerta_texto;
			icone = R.drawable.ic_sync;
			rotulo = R.string.conexaoVerificando;
		}
		String texto = getString(rotulo);
		if (naFila > 0) {
			texto = getString(R.string.conexaoNaFila, texto, naFila);
		}
		int corTexto = ContextCompat.getColor(this, cor);
		layoutConexaoServidor.setBackgroundTintList(ColorStateList.valueOf(ContextCompat.getColor(this, fundo)));
		layoutConexaoServidor.setContentDescription(getString(R.string.descConexao, texto));
		tvStatusConexao.setText(texto);
		tvStatusConexao.setTextColor(corTexto);
		ivStatusConexao.setImageResource(icone);
		ivStatusConexao.setImageTintList(ColorStateList.valueOf(corTexto));
	}

	private void abrirDialogoConfigIp() {
		com.rfid.demo.utils.SyncManager sm = com.rfid.demo.utils.SyncManager.getInstance(this);

		LinearLayout container = new LinearLayout(this);
		container.setOrientation(LinearLayout.VERTICAL);

		LinearLayout linhaCampo = new LinearLayout(this);
		linhaCampo.setOrientation(LinearLayout.HORIZONTAL);
		linhaCampo.setGravity(android.view.Gravity.CENTER_VERTICAL);

		EditText input = new EditText(this);
		input.setSingleLine(true);
		input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
		input.setTransformationMethod(android.text.method.PasswordTransformationMethod.getInstance());
		input.setHint(R.string.hintConfigServidor);

		String urlAtual = sm.getServerUrl();
		String tokenAtual = com.rfid.demo.utils.SyncManager.gerarToken(urlAtual);
		if (!tokenAtual.isEmpty()) {
			input.setText(tokenAtual);
		} else {
			String host = urlAtual.replaceAll("^https?://", "").replaceAll("(:\\d+)?(/.*)?$", "").trim();
			if (host.matches("^\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}\\.\\d{1,3}$")) {
				input.setText(urlAtual);
			} else if (!host.isEmpty() && !host.equalsIgnoreCase("localhost") && !host.equals("127.0.0.1")) {
				input.setText(host);
			} else {
				input.setText("");
			}
		}
		input.setLayoutParams(new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f));

		ImageView btnOlho = new ImageView(this);
		btnOlho.setImageResource(R.drawable.ic_visibility);
		btnOlho.setContentDescription(getString(R.string.descMostrarOcultarEndereco));
		int pad = (int) (10 * getResources().getDisplayMetrics().density);
		btnOlho.setPadding(pad, pad, pad, pad);

		final boolean[] visivel = {false};
		btnOlho.setOnClickListener(v -> {
			visivel[0] = !visivel[0];
			if (visivel[0]) {
				input.setTransformationMethod(null);
				btnOlho.setImageResource(R.drawable.ic_visibility_off);
			} else {
				input.setTransformationMethod(android.text.method.PasswordTransformationMethod.getInstance());
				btnOlho.setImageResource(R.drawable.ic_visibility);
			}
			input.setSelection(input.getText().length());
		});

		linhaCampo.addView(input);
		linhaCampo.addView(btnOlho);
		container.addView(linhaCampo);

		// Ações rápidas: auto-preencher formato de endereço completo ou limpar
		LinearLayout linhaAcoes = new LinearLayout(this);
		linhaAcoes.setOrientation(LinearLayout.HORIZONTAL);
		linhaAcoes.setGravity(android.view.Gravity.CENTER_VERTICAL);
		linhaAcoes.setPadding(0, pad / 3, 0, pad / 3);

		Button btnPreencherUrl = new Button(this, null, android.R.attr.buttonStyleSmall);
		btnPreencherUrl.setText(R.string.btPreencherUrlCompleta);
		btnPreencherUrl.setTextSize(11);
		LinearLayout.LayoutParams lpPreencher = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1.0f);
		lpPreencher.setMargins(0, 0, pad / 2, 0);
		btnPreencherUrl.setLayoutParams(lpPreencher);
		btnPreencherUrl.setOnClickListener(v -> {
			input.setText("http://:5000");
			input.setSelection(7);
			visivel[0] = true;
			input.setTransformationMethod(null);
			btnOlho.setImageResource(R.drawable.ic_visibility_off);
			input.requestFocus();
		});

		Button btnLimpar = new Button(this, null, android.R.attr.buttonStyleSmall);
		btnLimpar.setText(R.string.btLimparCampo);
		btnLimpar.setTextSize(11);
		LinearLayout.LayoutParams lpLimpar = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
		btnLimpar.setLayoutParams(lpLimpar);
		btnLimpar.setOnClickListener(v -> {
			input.setText("");
			input.requestFocus();
		});

		linhaAcoes.addView(btnPreencherUrl);
		linhaAcoes.addView(btnLimpar);
		container.addView(linhaAcoes);

		TextView tvAjuda = new TextView(this);
		tvAjuda.setText(R.string.ajudaTokenConexao);
		tvAjuda.setTextSize(12);
		tvAjuda.setTextColor(ContextCompat.getColor(this, R.color.texto_secundario));
		tvAjuda.setPadding(0, pad / 2, 0, 0);
		container.addView(tvAjuda);

		new AlertDialog.Builder(this)
				.setTitle(R.string.tituloConfigServidor)
				.setMessage(R.string.msgConfigServidor)
				.setView(Dialogos.comMargem(this, container))
				.setPositiveButton(R.string.btSalvarTestar, (dialog, which) -> {
					String entrada = input.getText().toString().trim();
					if (!entrada.isEmpty()) {
						String novaUrl = com.rfid.demo.utils.SyncManager.resolverEnderecoServidor(entrada, MainActivity.this);
						if (!novaUrl.isEmpty()) {
							sm.setServerUrl(novaUrl);
							if (tvStatusConexao != null) {
								mostrarConexao(CONEXAO_VERIFICANDO, 0);
							}
							verificarStatusServidor();
						} else {
							Toast.makeText(MainActivity.this, R.string.msgTokenInvalido, Toast.LENGTH_LONG).show();
						}
					}
				})
				.setNegativeButton(R.string.btCancelar, null)
				.show();
	}


	public int xorString(String input) {
		int xorValue = 0;
		for (int i = 0; i < input.length(); i++) {
			xorValue ^= input.charAt(i);
		}
		return xorValue;
	}

	private void changeTabBottomBg(){
		int index = getTabHost().getCurrentTab();
		//调用tabhost中的getTabWidget()方法得到TabWidget
		TabWidget tabWidget = getTabHost().getTabWidget();
		for (int i = 0; i < tabWidget.getChildCount(); i++) {
			TextView tView = (TextView) tabWidget.getChildAt(i).findViewById(
					android.R.id.title);
			//修改背景
			tabWidget.getChildAt(i).setBackgroundResource( R.drawable.custom_tab_indicator);
//			tView.setTextSize(16);
			//字体
//			tView.setTypeface(Typeface.DEFAULT);
			if (myTabHost.getCurrentTab() == i) {
				tView.setTextColor(getResources().getColorStateList(R.color.white));
			} else {
				tView.setTextColor(getResources().getColorStateList(android.R.color.black));
			}
		}
	}


	//点击某一个选项卡切换该选项卡背景的方法
	private void changeTabBackGround(){
		//得到当前选中选项卡的索引
		int index = getTabHost().getCurrentTab();
		//调用tabhost中的getTabWidget()方法得到TabWidget
		TabWidget tabWidget = getTabHost().getTabWidget();
		//得到选项卡的数量
		int count = tabWidget.getChildCount();
		//循环判断，只有点中的索引值改变背景颜色，其他的则恢复未选中的颜色
		for(int i=0;i<count;i++){
			View view = tabWidget.getChildAt(i);
			if(index == i){
				view.setBackgroundResource(R.color.main_color);
			}else{
				view.setBackgroundResource(R.color.transparent_background_tab);
			}
		}
	}
	@Override
	public boolean onCreateOptionsMenu(Menu menu) {
		return true;
	}
	@Override
	protected void onResume() {
		super.onResume();
		RFIDSDKManager.getInstance().enableScanHead(false);
		if (runnableVerificarConexao != null) {
			handlerConexao.removeCallbacks(runnableVerificarConexao);
			handlerConexao.post(runnableVerificarConexao);
		}
	}

	@Override
	protected void onPause() {
		super.onPause();
		RFIDSDKManager.getInstance().enableScanHead(true);
		if (runnableVerificarConexao != null) {
			handlerConexao.removeCallbacks(runnableVerificarConexao);
		}
	}

	@Override
	protected void onDestroy() {
		super.onDestroy();

	}
	/**
	 * Return to exit program twice
	 * 两次返回退出程序
	 */
	private long firstTime = 0;
	@Override
	public boolean dispatchKeyEvent(KeyEvent event) {
		int keyCode = event.getKeyCode();
		if (ScanActivity.isTriggerKey(keyCode)) {
			Activity current = getCurrentActivity();
			if (current != null && current.dispatchKeyEvent(event)) {
				return true;
			}
		}

		long secondTime = System.currentTimeMillis();
		if(event.getKeyCode() == KeyEvent.KEYCODE_BACK &&  event.getAction() == KeyEvent.ACTION_DOWN && event.getRepeatCount() == 0){
			// Em Pendências, dentro de uma sala, voltar primeiro devolve à lista de salas.
			Activity atual = getCurrentActivity();
			if (atual instanceof TagsSalaActivity && ((TagsSalaActivity) atual).voltarParaSalas()) {
				return true;
			}
			// No Inventário, com uma sala em leitura, voltar pergunta se encerra a varredura.
			if (atual instanceof ScanActivity && ((ScanActivity) atual).voltar()) {
				return true;
			}
			// Fora do Painel, voltar leva a ele; só nele o voltar duplo fecha o app.
			if (myTabHost.getCurrentTab() != ABA_PAINEL) {
				myTabHost.setCurrentTab(ABA_PAINEL);
				return true;
			}
			if (secondTime - firstTime < 2000) {
				RFIDSDKManager.getInstance().release();
				finish();
//				System.exit(0);
			} else {
				Toast.makeText(this, getString(R.string.press_again_exit_app), Toast.LENGTH_SHORT).show();
				firstTime = System.currentTimeMillis();
			}
			return true;
		}

		return super.dispatchKeyEvent(event);
	}


	/**
	 * 设置状态栏颜色
	 * @param activity
	 * @param color
	 */
	private void setStatusBarColor(Activity activity, int color){
		Window window=activity.getWindow();
		//取消状态栏透明
		window.clearFlags(WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS);
		//添加Flag把状态栏设为可绘制模式
		window.addFlags(WindowManager.LayoutParams.FLAG_DRAWS_SYSTEM_BAR_BACKGROUNDS);
		//设置状态栏颜色
		window.setStatusBarColor(color);
		//设置系统状态栏处于可见状态
		window.getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_VISIBLE);
		//让view不根据系统窗口来调整自己的布局
		ViewGroup mContentView = (ViewGroup) window.findViewById(Window.ID_ANDROID_CONTENT);
		View mChildView = mContentView.getChildAt(0);
		if (mChildView != null) {
			ViewCompat.setFitsSystemWindows(mChildView, false);
			ViewCompat.requestApplyInsets(mChildView);
		}
	}
}
