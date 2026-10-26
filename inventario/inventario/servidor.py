"""Servidor HTTP e Interface Web para gestão de bens e sincronização com o Urovo DT50.

Roda no computador da empresa e oferece:
1. Interface Web para cadastro/exclusão/listagem de bens (CRUD simples).
2. Endpoints REST para o Urovo DT50:
   - GET /api/status          -> Health-check para detecção de reconexão
   - GET /api/bens            -> Lista de bens esperados por sala (para carga offline)
   - GET /api/salas           -> Lista de salas cadastradas
   - POST /api/bens           -> Cadastra novo bem
   - DELETE /api/bens/<pat>   -> Exclui bem
   - POST /api/sincronizar    -> Recebe varreduras e resolução de pendências do Urovo
"""

import csv
import io
import os
import socket
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path
from flask import Flask, jsonify, make_response, redirect, render_template_string, request, url_for

from . import conferencia, esquema, importador

app = Flask(__name__)

# Buffer temporário para ponte de captura rápida de tag via Urovo
tag_capturada_recente = {"epc": None, "timestamp": 0}

# Rastreamento de conexão do coletor Urovo
ultimo_contato_coletor = {"timestamp": None, "ip": None, "acao": None}


def registrar_contato_coletor(acao="comunicacao"):
    global ultimo_contato_coletor
    ultimo_contato_coletor = {
        "timestamp": time.time(),
        "ip": request.remote_addr,
        "acao": acao,
    }


# Caminho padrão do banco de dados SQLite
CAMINHO_DB = Path(__file__).resolve().parents[1] / "dados" / "inventario.db"


def get_db():
    return esquema.conectar(CAMINHO_DB)


def obter_ip_local():
    """Detecta o IP do computador na rede local (Wi-Fi/Ethernet) para o coletor se conectar."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FIEA Inventário - Gestão de Bens & Servidor Local</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .navbar { background-color: #1e3a8a; }
        .card { border-radius: 10px; border: none; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .badge-metal { background-color: #0284c7; }
        .badge-rfid { background-color: #059669; }
        .ip-box { background: #e0f2fe; border: 1px solid #bae6fd; border-radius: 8px; padding: 12px; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark mb-4">
        <div class="container-fluid px-4">
            <a class="navbar-brand mb-0 h1 text-decoration-none" href="/">📦 FIEA Coleta RFID — Servidor Local</a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="btn btn-sm btn-light text-primary fw-bold">📋 Bens Cadastrados</a>
                <a href="/salas" class="btn btn-sm btn-outline-light">🏢 Gerenciar Salas</a>
                <a href="/relatorio" class="btn btn-sm btn-outline-light">📊 Relatório de Inventário</a>
                <span class="badge bg-success ms-2">Servidor Ativo</span>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Banner de Conexão com o Urovo -->
        <div class="ip-box mb-4 d-flex justify-content-between align-items-center">
            <div>
                <h6 class="text-primary mb-1 fw-bold">📡 Conexão com o Coletor Urovo DT50 (Mesma Rede Wi-Fi)</h6>
                <p class="mb-0 text-muted small">
                    Endereço para configurar no coletor: 
                    <strong class="text-dark fs-6 font-monospace">http://{{ ip_local }}:5000</strong>
                </p>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div id="badgeColetor" class="badge bg-secondary p-2 d-flex align-items-center gap-2" style="font-size: 0.85rem;">
                    <span id="dotColetor" class="rounded-circle bg-white" style="width: 8px; height: 8px; display: inline-block;"></span>
                    <span id="textoColetor">⚪ Coletor: Aguardando Wi-Fi</span>
                </div>
                <div class="text-end">
                    <span class="badge bg-light text-primary border">Total de Bens: {{ bens|length }}</span>
                    <span class="badge bg-light text-primary border">Salas: {{ salas|length }}</span>
                </div>
            </div>
        </div>

        {% if mensagem %}
        <div class="alert alert-{{ tipo_msg or 'info' }} alert-dismissible fade show" role="alert">
            {{ mensagem }}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        {% endif %}

        <div class="row">
            <!-- Formulário de Cadastro -->
            <div class="col-lg-4 mb-4">
                <div class="card h-100">
                    <div class="card-header bg-white py-3">
                        <h6 class="mb-0 fw-bold">➕ Cadastrar Novo Bem</h6>
                    </div>
                    <div class="card-body">
                        <form method="POST" action="/cadastrar">
                            <div class="mb-3">
                                <label class="form-label small fw-semibold">Patrimônio / Tombo *</label>
                                <input type="text" name="patrimonio" class="form-control" placeholder="Ex: 10001" required>
                            </div>
                            <div class="mb-3">
                                <label class="form-label small fw-semibold">Descrição do Bem *</label>
                                <input type="text" name="descricao" class="form-control" placeholder="Ex: Notebook Dell Latitude" required>
                            </div>
                            <div class="mb-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <label class="form-label small fw-semibold mb-0">EPC da Tag RFID</label>
                                    <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2" id="btnCapturarTag" style="font-size: 0.78rem;">
                                        📡 Capturar do Urovo
                                    </button>
                                </div>
                                <input type="text" name="epc" id="campoEpc" class="form-control font-monospace" placeholder="Ex: E2801160600002162A7A1C57">
                                <div id="statusCaptura" class="form-text small">Deixe em branco se ainda não foi etiquetado.</div>
                            </div>
                            <div class="mb-3">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <label class="form-label small fw-semibold mb-0">Sala Esperada *</label>
                                    <a href="/salas" class="text-decoration-none small text-primary" style="font-size: 0.76rem;">➕ Nova Sala</a>
                                </div>
                                <select name="sala" class="form-select" required>
                                    <option value="" disabled selected>Selecione uma sala...</option>
                                    {% for s in salas %}
                                    <option value="{{ s.id }}">{{ s.nome }} ({{ s.unidade }})</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="form-check mb-4">
                                <input class="form-check-input" type="checkbox" name="metalico" id="checkMetal">
                                <label class="form-check-label small" for="checkMetal">
                                    Bem metálico (requer tag on-metal)
                                </label>
                            </div>
                            <button type="submit" class="btn btn-primary w-100 py-2">Salvar Bem no Cadastro</button>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Tabela de Bens Cadastrados -->
            <div class="col-lg-8 mb-4">
                <div class="card h-100">
                    <div class="card-header bg-white py-3 d-flex justify-content-between align-items-center">
                        <h6 class="mb-0 fw-bold">📋 Relação Patrimonial de Bens</h6>
                        <input type="text" id="filtro" class="form-control form-control-sm w-50" placeholder="🔍 Filtrar por patrimônio, descrição ou sala...">
                    </div>
                    <div class="card-body p-0">
                        <div class="table-responsive" style="max-height: 520px; overflow-y: auto;">
                            <table class="table table-hover align-middle mb-0" id="tabelaBens">
                                <thead class="table-light sticky-top">
                                    <tr class="small text-muted">
                                        <th>Patrimônio</th>
                                        <th>Descrição</th>
                                        <th>Sala</th>
                                        <th>Tag EPC</th>
                                        <th>Metálico</th>
                                        <th class="text-center">Ações</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for b in bens %}
                                    <tr>
                                        <td class="fw-bold">{{ b.patrimonio }}</td>
                                        <td>{{ b.descricao }}</td>
                                        <td><span class="badge bg-secondary">{{ b.sala or 'Sem sala' }}</span></td>
                                        <td class="font-monospace small">
                                            {% if b.epc %}
                                                <span class="badge bg-light text-dark border">{{ b.epc }}</span>
                                            {% else %}
                                                <span class="text-muted fst-italic">Sem tag</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            {% if b.metalico %}
                                                <span class="badge badge-metal">Sim</span>
                                            {% else %}
                                                <span class="text-muted small">Não</span>
                                            {% endif %}
                                        </td>
                                        <td class="text-center">
                                            <form method="POST" action="/excluir/{{ b.patrimonio }}" onsubmit="return confirm('Excluir o bem {{ b.patrimonio }}?');" style="display:inline;">
                                                <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-2" title="Excluir">✕</button>
                                            </form>
                                        </td>
                                    </tr>
                                    {% else %}
                                    <tr>
                                        <td colspan="6" class="text-center py-4 text-muted">
                                            Nenhum bem cadastrado ainda. Preencha o formulário ao lado ou importe sua planilha via terminal.
                                        </td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        document.getElementById('filtro').addEventListener('keyup', function() {
            let valor = this.value.toLowerCase();
            let linhas = document.querySelectorAll('#tabelaBens tbody tr');
            linhas.forEach(linha => {
                let texto = linha.innerText.toLowerCase();
                linha.style.display = texto.includes(valor) ? '' : 'none';
            });
        });

        // Captura rápida de tag RFID via Urovo (Ponte Coletor -> PC)
        let pollingCaptura = null;
        const btnCapturar = document.getElementById('btnCapturarTag');
        const campoEpc = document.getElementById('campoEpc');
        const statusCaptura = document.getElementById('statusCaptura');

        if (btnCapturar) {
            btnCapturar.addEventListener('click', function() {
                if (pollingCaptura) {
                    clearInterval(pollingCaptura);
                    pollingCaptura = null;
                    btnCapturar.innerHTML = '📡 Capturar do Urovo';
                    btnCapturar.className = 'btn btn-sm btn-outline-primary py-0 px-2';
                    statusCaptura.innerHTML = 'Deixe em branco se ainda não foi etiquetado.';
                    statusCaptura.className = 'form-text small text-muted';
                    return;
                }

                btnCapturar.innerHTML = '⏳ Aguardando... (Cancelar)';
                btnCapturar.className = 'btn btn-sm btn-warning py-0 px-2';
                statusCaptura.innerHTML = '<span class="spinner-border spinner-border-sm text-primary me-1"></span> Aproxime o Urovo da etiqueta e clique em <strong>"Transmitir Tag ao PC"</strong> no coletor...';
                statusCaptura.className = 'form-text small text-primary fw-semibold';

                pollingCaptura = setInterval(() => {
                    fetch('/api/tag-capturada')
                        .then(r => r.json())
                        .then(data => {
                            if (data && data.epc) {
                                campoEpc.value = data.epc;
                                campoEpc.classList.add('is-valid');
                                statusCaptura.innerHTML = '✅ Tag capturada com sucesso: <code>' + data.epc + '</code>';
                                statusCaptura.className = 'form-text small text-success fw-bold';
                                clearInterval(pollingCaptura);
                                pollingCaptura = null;
                                btnCapturar.innerHTML = '📡 Capturar do Urovo';
                                btnCapturar.className = 'btn btn-sm btn-outline-success py-0 px-2';
                            }
                        })
                        .catch(() => {});
                }, 800);
            });
        }

        // Monitoramento em tempo real do status de conexão com o coletor Urovo
        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        badge.className = 'badge bg-success p-2 d-flex align-items-center gap-2';
                        dot.className = 'rounded-circle bg-white spinner-grow spinner-grow-sm';
                        dot.style.width = '8px';
                        dot.style.height = '8px';
                        texto.innerText = `🟢 Coletor Conectado (${data.ip || 'Urovo'} · há ${data.segundos_atras}s)`;
                    } else {
                        badge.className = 'badge bg-secondary p-2 d-flex align-items-center gap-2';
                        dot.className = 'rounded-circle bg-white';
                        dot.style.width = '8px';
                        dot.style.height = '8px';
                        texto.innerText = '⚪ Coletor: Aguardando Wi-Fi';
                    }
                })
                .catch(() => {});
        }
        setInterval(atualizarStatusColetor, 3000);
        atualizarStatusColetor();
    </script>
</body>
</html>
"""


HTML_SALAS_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FIEA Inventário - Gestão de Salas</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        .navbar { background-color: #1e3a8a; }
        .card { border-radius: 10px; border: none; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .ip-box { background: #e0f2fe; border: 1px solid #bae6fd; border-radius: 8px; padding: 12px; }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-dark mb-4">
        <div class="container-fluid px-4">
            <a class="navbar-brand mb-0 h1 text-decoration-none" href="/">📦 FIEA Coleta RFID — Servidor Local</a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="btn btn-sm btn-outline-light">📋 Bens Cadastrados</a>
                <a href="/salas" class="btn btn-sm btn-light text-primary fw-bold">🏢 Gerenciar Salas</a>
                <a href="/relatorio" class="btn btn-sm btn-outline-light">📊 Relatório de Inventário</a>
                <span class="badge bg-success ms-2">Servidor Ativo</span>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4">
        <!-- Banner de Conexão com o Urovo -->
        <div class="ip-box mb-4 d-flex justify-content-between align-items-center">
            <div>
                <h6 class="text-primary mb-1 fw-bold">🏢 Cadastro e Gestão de Salas</h6>
                <p class="mb-0 text-muted small">
                    As salas cadastradas aqui alimentam o coletor Urovo DT50 e o menu de seleção no cadastro de bens.
                </p>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div id="badgeColetor" class="badge bg-secondary p-2 d-flex align-items-center gap-2" style="font-size: 0.85rem;">
                    <span id="dotColetor" class="rounded-circle bg-white" style="width: 8px; height: 8px; display: inline-block;"></span>
                    <span id="textoColetor">⚪ Coletor: Aguardando Wi-Fi</span>
                </div>
                <div class="text-end">
                    <span class="badge bg-light text-primary border">Total de Salas: {{ salas|length }}</span>
                </div>
            </div>
        </div>

        {% if mensagem %}
        <div class="alert alert-{{ tipo_msg or 'info' }} alert-dismissible fade show" role="alert">
            {{ mensagem }}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        {% endif %}

        <div class="row">
            <!-- Formulário de Cadastro de Sala -->
            <div class="col-lg-4 mb-4">
                <div class="card h-100">
                    <div class="card-header bg-white py-3">
                        <h6 class="mb-0 fw-bold">➕ Cadastrar Nova Sala</h6>
                    </div>
                    <div class="card-body">
                        <form method="POST" action="/salas/cadastrar">
                            <div class="mb-3">
                                <label class="form-label small fw-semibold">Nome da Sala *</label>
                                <input type="text" name="nome" class="form-control" placeholder="Ex: EASY, Lab 1, Almoxarifado" required autofocus>
                                <div class="form-text small">Nome exato como será selecionado no inventário.</div>
                            </div>
                            <div class="mb-3">
                                <label class="form-label small fw-semibold">Unidade *</label>
                                <input type="text" name="unidade" class="form-control" value="UFAL" placeholder="Ex: UFAL" required>
                            </div>
                            <button type="submit" class="btn btn-primary w-100 py-2">Salvar Sala</button>
                            <a href="/" class="btn btn-link w-100 text-center text-muted small mt-2">← Voltar para Cadastro de Bens</a>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Tabela de Salas Cadastradas -->
            <div class="col-lg-8 mb-4">
                <div class="card h-100">
                    <div class="card-header bg-white py-3 d-flex justify-content-between align-items-center">
                        <h6 class="mb-0 fw-bold">📋 Salas Cadastradas</h6>
                        <span class="badge bg-primary">{{ salas|length }} salas</span>
                    </div>
                    <div class="card-body p-0">
                        <div class="table-responsive" style="max-height: 520px; overflow-y: auto;">
                            <table class="table table-hover align-middle mb-0">
                                <thead class="table-light small">
                                    <tr>
                                        <th>Nome da Sala</th>
                                        <th>Unidade</th>
                                        <th>Bens Vinculados</th>
                                        <th class="text-end">Ações</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for s in salas %}
                                    <tr>
                                        <td class="fw-bold">{{ s.nome }}</td>
                                        <td><span class="badge bg-light text-dark border">{{ s.unidade }}</span></td>
                                        <td>
                                            {% if s.total_bens > 0 %}
                                            <span class="badge bg-success-subtle text-success border border-success">{{ s.total_bens }} bem(ns)</span>
                                            {% else %}
                                            <span class="text-muted small">0 bens</span>
                                            {% endif %}
                                        </td>
                                        <td class="text-end">
                                            <form method="POST" action="/salas/excluir/{{ s.id }}" style="display:inline;" onsubmit="return confirm('Deseja realmente excluir a sala {{ s.nome }}?');">
                                                <button type="submit" class="btn btn-outline-danger btn-sm py-0 px-2" style="font-size: 0.8rem;">Excluir</button>
                                            </form>
                                        </td>
                                    </tr>
                                    {% else %}
                                    <tr>
                                        <td colspan="4" class="text-center text-muted py-4">Nenhuma sala cadastrada ainda. Utilize o formulário ao lado.</td>
                                    </tr>
                                    {% endfor %}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        badge.className = 'badge bg-success p-2 d-flex align-items-center gap-2';
                        dot.className = 'rounded-circle bg-white spinner-grow spinner-grow-sm';
                        dot.style.width = '8px';
                        dot.style.height = '8px';
                        texto.innerText = `🟢 Coletor Conectado (${data.ip || 'Urovo'} · há ${data.segundos_atras}s)`;
                    } else {
                        badge.className = 'badge bg-secondary p-2 d-flex align-items-center gap-2';
                        dot.className = 'rounded-circle bg-white';
                        dot.style.width = '8px';
                        dot.style.height = '8px';
                        texto.innerText = '⚪ Coletor: Aguardando Wi-Fi';
                    }
                })
                .catch(() => {});
        }
        setInterval(atualizarStatusColetor, 3000);
        atualizarStatusColetor();
    </script>
</body>
</html>
"""



HTML_RELATORIO_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FIEA Inventário - Relatório de Auditoria e Conferência Patrimonial</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background-color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; color: #1e293b; }
        .navbar { background-color: #1e3a8a; }
        .card { border-radius: 10px; border: none; box-shadow: 0 2px 8px rgba(0,0,0,0.06); }
        .ip-box { background: #e0f2fe; border: 1px solid #bae6fd; border-radius: 8px; padding: 12px; }
        .kpi-card { border-radius: 10px; transition: transform 0.15s ease; }
        .kpi-card:hover { transform: translateY(-2px); }
        .badge-metal { background-color: #0284c7; }
        
        /* Área de Arraste de Arquivos (Drag and Drop) */
        .drop-zone {
            border: 2px dashed #3b82f6;
            border-radius: 12px;
            background-color: #f0f7ff;
            transition: all 0.2s ease-in-out;
            cursor: pointer;
        }
        .drop-zone.dragover {
            background-color: #dbeafe;
            border-color: #1d4ed8;
            transform: scale(1.01);
        }

        /* Estilização para Impressão / Exportação PDF */
        @media print {
            @page {
                size: A4 portrait;
                margin: 12mm 10mm 15mm 10mm;
            }
            body {
                background-color: #fff !important;
                color: #000 !important;
                font-family: Arial, Helvetica, sans-serif !important;
                font-size: 9.5pt;
            }
            .no-print {
                display: none !important;
            }
            .only-print {
                display: block !important;
            }
            .card {
                border: 1px solid #ccc !important;
                box-shadow: none !important;
                margin-bottom: 15px !important;
            }
            .table {
                border: 1px solid #666 !important;
                font-size: 8.5pt !important;
            }
            .table th, .table td {
                border: 1px solid #bbb !important;
                padding: 4px 6px !important;
            }
            .table thead th {
                background-color: #f1f5f9 !important;
                color: #000 !important;
                border-bottom: 2px solid #333 !important;
            }
            tr {
                page-break-inside: avoid !important;
            }
            .badge {
                border: 1px solid #333 !important;
                background: transparent !important;
                color: #000 !important;
                font-weight: 600 !important;
                padding: 2px 4px !important;
            }
            .table-responsive, #containerTabelaSobras {
                overflow: visible !important;
                max-height: none !important;
                height: auto !important;
            }
            .badge-success, .text-success { border-color: #166534 !important; color: #166534 !important; }
            .badge-warning, .text-warning { border-color: #854d0e !important; color: #854d0e !important; }
            .badge-danger, .text-danger { border-color: #991b1b !important; color: #991b1b !important; }
            .badge-secondary, .text-secondary { border-color: #475569 !important; color: #475569 !important; }
            .badge-info, .text-info { border-color: #0e7490 !important; color: #0e7490 !important; }
        }
        @media screen {
            .only-print {
                display: none !important;
            }
        }
    </style>
</head>
<body>
    <!-- Navbar (Oculto na Impressão) -->
    <nav class="navbar navbar-expand-lg navbar-dark mb-4 no-print">
        <div class="container-fluid px-4">
            <a class="navbar-brand mb-0 h1 text-decoration-none" href="/">📦 FIEA Coleta RFID — Servidor Local</a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="btn btn-sm btn-outline-light">📋 Bens Cadastrados</a>
                <a href="/salas" class="btn btn-sm btn-outline-light">🏢 Gerenciar Salas</a>
                <a href="/relatorio" class="btn btn-sm btn-light text-primary fw-bold">📊 Relatório de Inventário</a>
                <span class="badge bg-success ms-2">Servidor Ativo</span>
            </div>
        </div>
    </nav>

    <!-- Cabeçalho Oficial para Impressão / PDF -->
    <div class="container-fluid px-4 only-print">
        <div class="text-center pb-3 mb-3 border-bottom border-2 border-dark">
            <h4 class="fw-bold mb-1 text-uppercase">FEDERAÇÃO DAS INDÚSTRIAS DO ESTADO DE ALAGOAS — FIEA</h4>
            <h6 class="text-uppercase text-muted mb-2">UNIVERSIDADE FEDERAL DE ALAGOAS — UFAL</h6>
            <h5 class="fw-bold mb-0 text-primary">RELATÓRIO DE AUDITORIA E CONFERÊNCIA PATRIMONIAL (RFID)</h5>
        </div>
        <div class="row mb-3 small">
            <div class="col-6">
                <strong>ID da Sessão:</strong> {{ sessao_atual.id if sessao_atual else 'N/D' }}<br>
                <strong>Unidade Auditada:</strong> {{ sessao_atual.unidade if sessao_atual else 'UFAL' }}<br>
                <strong>Operador Responsável:</strong> {{ sessao_atual.operador if sessao_atual else 'Não informado' }}
            </div>
            <div class="col-6 text-end">
                <strong>Data da Auditoria:</strong> {{ sessao_atual.inicio if sessao_atual and sessao_atual.inicio else 'Em aberto' }}<br>
                <strong>Data de Emissão do Relatório:</strong> <span class="data-emissao-print"></span><br>
                <strong>Total de Varreduras:</strong> {{ varreduras|length }} sala(s) auditada(s)
            </div>
        </div>
    </div>

    <div class="container-fluid px-4">
        <!-- Banner de Conexão com o Urovo (No-print) -->
        <div class="ip-box mb-4 d-flex justify-content-between align-items-center no-print">
            <div>
                <h6 class="text-primary mb-1 fw-bold">📊 Relatório de Conferência e Auditoria Patrimonial</h6>
                <p class="mb-0 text-muted small">
                    Confronto automático entre o cadastro mestre de bens e as leituras de campo realizadas pelo coletor Urovo DT50.
                </p>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div id="badgeColetor" class="badge bg-secondary p-2 d-flex align-items-center gap-2" style="font-size: 0.85rem;">
                    <span id="dotColetor" class="rounded-circle bg-white" style="width: 8px; height: 8px; display: inline-block;"></span>
                    <span id="textoColetor">⚪ Coletor: Aguardando Wi-Fi</span>
                </div>
                <div class="text-end">
                    <span class="badge bg-light text-primary border">Sessões: {{ sessoes|length }}</span>
                </div>
            </div>
        </div>

        {% if mensagem %}
        <div class="alert alert-{{ tipo_msg or 'info' }} alert-dismissible fade show no-print" role="alert">
            {{ mensagem }}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
        </div>
        {% endif %}

        <!-- Barra Superior de Controle e Seleção de Sessão (No-print) -->
        <div class="card mb-4 no-print">
            <div class="card-body p-3">
                <div class="row g-2 align-items-center">
                    <div class="col-lg-5 col-md-12">
                        <div class="d-flex justify-content-between align-items-center mb-1">
                            <label class="form-label small fw-bold text-muted mb-0">Selecionar Sessão de Auditoria:</label>
                            {% if sessao_id %}
                            <form method="POST" action="/relatorio/excluir-sessao/{{ sessao_id }}" style="display:inline;" onsubmit="return confirm('Deseja realmente excluir o histórico desta sessão ({{ sessao_id }})? Todas as leituras e varreduras desta coleta serão apagadas.');">
                                <button type="submit" class="btn btn-link text-danger p-0 text-decoration-none small" style="font-size: 0.78rem;" title="Excluir esta sessão de inventário">
                                    🗑️ Excluir esta Sessão
                                </button>
                            </form>
                            {% endif %}
                        </div>
                        <select class="form-select form-select-sm fw-semibold" onchange="window.location.href='/relatorio?sessao=' + this.value">
                            {% for s in sessoes %}
                            <option value="{{ s.id }}" {% if s.id == sessao_id %}selected{% endif %}>
                                Sessão {{ s.id }} — {{ s.inicio or 'Data N/D' }} (Op: {{ s.operador or 'N/D' }} · {{ s.total_varreduras }} varreduras)
                            </option>
                            {% else %}
                            <option value="" disabled selected>Nenhuma sessão cadastrada ainda</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-lg-7 col-md-12 d-flex justify-content-lg-end gap-2 pt-lg-3">
                        <button type="button" class="btn btn-sm btn-outline-primary" onclick="toggleAreaImportacao()">
                            📁 Importar CSVs Offline
                        </button>
                        {% if sessao_id %}
                        <a href="/relatorio/{{ sessao_id }}/exportar-csv" class="btn btn-sm btn-outline-success">
                            📥 Baixar CSV / Excel
                        </a>
                        <div class="d-inline-flex align-items-center bg-light border rounded px-2 py-1">
                            <div class="form-check form-check-inline mb-0 me-2 small">
                                <input class="form-check-input" type="checkbox" id="checkImprimirSobras" checked onchange="toggleImprimirSobras(this.checked)">
                                <label class="form-check-label text-muted small" for="checkImprimirSobras" title="Marque para incluir a lista de tags não cadastradas no PDF impresso">Incluir Sobras no PDF</label>
                            </div>
                            <button type="button" class="btn btn-sm btn-primary" onclick="imprimirRelatorio()" title="Gera o PDF oficial através do diálogo de impressão do navegador">
                                🖨️ Exportar PDF / Imprimir
                            </button>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>

        <!-- Área de Importação Amigável de Arquivos Offline (No-print) -->
        <div class="card mb-4 no-print border-primary border-opacity-50" id="cardImportacao">
            <div class="card-header bg-white py-3 d-flex justify-content-between align-items-center">
                <div class="d-flex align-items-center gap-2">
                    <span class="fs-5">📥</span>
                    <div>
                        <h6 class="mb-0 fw-bold text-primary">Importação de Arquivos da Coleta Offline (Urovo DT50)</h6>
                        <small class="text-muted">Como enviar os arquivos sem confusão com nomes compridos</small>
                    </div>
                </div>
                <button type="button" class="btn-close" onclick="toggleAreaImportacao()"></button>
            </div>
            <div class="card-body">
                <!-- Passo a passo visual amigável -->
                <div class="row g-3 mb-4">
                    <div class="col-md-4">
                        <div class="p-3 bg-light rounded-3 h-100 border">
                            <div class="d-flex align-items-center gap-2 mb-2">
                                <span class="badge bg-primary rounded-pill">Passo 1</span>
                                <strong class="small">Conecte o Coletor</strong>
                            </div>
                            <p class="text-muted small mb-0">
                                Conecte o cabo USB do DT50 ao PC (modo "Transferência de Arquivos") e abra a pasta <code>Download/FieaColeta</code>.
                            </p>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="p-3 bg-light rounded-3 h-100 border border-primary border-opacity-25">
                            <div class="d-flex align-items-center gap-2 mb-2">
                                <span class="badge bg-primary rounded-pill">Passo 2</span>
                                <strong class="small">Arraste Todos os CSVs</strong>
                            </div>
                            <p class="text-muted small mb-0">
                                Selecione <strong>todos</strong> os arquivos <code>.csv</code> de uma vez e arraste para o quadro abaixo. Não precisa se preocupar com os nomes dos arquivos!
                            </p>
                        </div>
                    </div>
                    <div class="col-md-4">
                        <div class="p-3 bg-light rounded-3 h-100 border">
                            <div class="d-flex align-items-center gap-2 mb-2">
                                <span class="badge bg-success rounded-pill">Passo 3</span>
                                <strong class="small">Pronto! Auditoria Gerada</strong>
                            </div>
                            <p class="text-muted small mb-0">
                                O sistema identifica as sessões e varreduras pelo conteúdo interno, salva no banco e abre a conferência completa.
                            </p>
                        </div>
                    </div>
                </div>

                <!-- Quadro de Arraste / Seleção Multi-Arquivo -->
                <form action="/relatorio/importar-csv" method="POST" enctype="multipart/form-data" id="formUploadCsv">
                    <div id="dropZone" class="drop-zone p-4 text-center">
                        <input type="file" name="arquivos" id="inputFileCsv" multiple accept=".csv" class="d-none">
                        <div id="dropPrompt">
                            <div class="fs-1 text-primary mb-2">📂</div>
                            <h6 class="fw-bold mb-1">Arraste os arquivos CSV aqui ou clique para selecionar do PC</h6>
                            <p class="text-muted small mb-0">
                                Dica: Você pode arrastar <code>sessao_*.csv</code> e <code>leituras_*.csv</code> juntos. O sistema agrupa automaticamente.
                            </p>
                        </div>
                        <div id="fileListContainer" class="mt-3 text-start small d-none"></div>
                        <div id="btnContainer" class="mt-3 d-none">
                            <button type="submit" id="btnEnviarCsv" class="btn btn-success px-4 py-2 fw-semibold">
                                🚀 Processar Arquivos Selecionados e Atualizar Relatório
                            </button>
                        </div>
                    </div>
                </form>
            </div>
        </div>

        {% if sessao_atual and relatorio %}
        <!-- Detalhes da Sessão Atual e Varreduras -->
        <div class="card mb-4">
            <div class="card-body p-3">
                <div class="row align-items-center">
                    <div class="col-md-6 mb-2 mb-md-0">
                        <span class="badge bg-primary me-2">Sessão: {{ sessao_atual.id }}</span>
                        <span class="badge bg-secondary me-2">Unidade: {{ sessao_atual.unidade }}</span>
                        <span class="badge bg-light text-dark border">Operador: {{ sessao_atual.operador }}</span>
                    </div>
                    <div class="col-md-6 text-md-end text-muted small">
                        <strong>Varreduras realizadas nesta sessão:</strong>
                        <div class="d-flex flex-wrap justify-content-md-end gap-1 mt-1">
                            {% for v in varreduras %}
                            <span class="badge bg-light text-primary border">
                                Sala {{ v.sala or 'Não Identificada' }} (v{{ "%02d"|format(v.numero) }} · {{ v.total_etiquetas }} tags)
                            </span>
                            {% else %}
                            <span class="badge bg-light text-muted border">Nenhuma varredura registrada</span>
                            {% endfor %}
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Indicadores / Cards de KPI -->
        <div class="row g-3 mb-4">
            <!-- Total de Bens -->
            <div class="col-xl-2 col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-primary">
                    <div class="text-muted small fw-semibold text-uppercase">Total Esperado</div>
                    <div class="fs-3 fw-bold text-dark my-1">{{ relatorio.bens|length }}</div>
                    <div class="text-muted small">Bens no cadastro</div>
                </div>
            </div>
            <!-- Localizados -->
            <div class="col-xl-2 col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-success">
                    <div class="text-success small fw-semibold text-uppercase">🟢 Localizados</div>
                    <div class="fs-3 fw-bold text-success my-1">{{ localizados|length }}</div>
                    <div class="text-muted small">{{ pct_localizados }}% de acurácia</div>
                </div>
            </div>
            <!-- Fora do Local -->
            <div class="col-xl-2 col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-warning">
                    <div class="text-warning small fw-semibold text-uppercase">🟡 Fora do Local</div>
                    <div class="fs-3 fw-bold text-warning my-1">{{ fora_do_local|length }}</div>
                    <div class="text-muted small">Sala trocada / divergente</div>
                </div>
            </div>
            <!-- Não Localizados -->
            <div class="col-xl-2 col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-danger">
                    <div class="text-danger small fw-semibold text-uppercase">🔴 Não Localizados</div>
                    <div class="fs-3 fw-bold text-danger my-1">{{ nao_localizados|length }}</div>
                    <div class="text-muted small">Faltantes na sala</div>
                </div>
            </div>
            <!-- Sem Cobertura -->
            <div class="col-xl-2 col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-secondary">
                    <div class="text-secondary small fw-semibold text-uppercase">⚪ Sem Cobertura</div>
                    <div class="fs-3 fw-bold text-secondary my-1">{{ sem_cobertura|length }}</div>
                    <div class="text-muted small">Sala não auditada</div>
                </div>
            </div>
            <!-- Não Cadastrados (Sobras) -->
            <div class="col-xl-2 col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-info">
                    <div class="text-info small fw-semibold text-uppercase">🔵 Tags Sobrando</div>
                    <div class="fs-3 fw-bold text-info my-1">{{ nao_cadastrados|length }}</div>
                    <div class="text-muted small">Lidos sem cadastro</div>
                </div>
            </div>
        </div>

        <!-- Barra de Filtros e Busca (No-print) -->
        <div class="card mb-4 no-print">
            <div class="card-body p-3">
                <div class="row g-2 align-items-center">
                    <div class="col-lg-8 col-md-12 d-flex flex-wrap gap-1">
                        <button type="button" class="btn btn-sm btn-primary btn-filtro" onclick="filtrarTabela('todos', this)">
                            Todos ({{ relatorio.bens|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-success btn-filtro" onclick="filtrarTabela('localizado', this)">
                            🟢 Localizados ({{ localizados|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-warning btn-filtro" onclick="filtrarTabela('fora do local', this)">
                            🟡 Fora do Local ({{ fora_do_local|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-danger btn-filtro" onclick="filtrarTabela('nao localizado', this)">
                            🔴 Faltantes ({{ nao_localizados|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="filtrarTabela('sem cobertura', this)">
                            ⚪ Sem Cobertura ({{ sem_cobertura|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-info btn-filtro" onclick="rolarParaSobras()">
                            🔵 Tags Sobrando ({{ nao_cadastrados|length }})
                        </button>
                    </div>
                    <div class="col-lg-4 col-md-12">
                        <input type="text" id="inputBuscaRelatorio" class="form-control form-control-sm" placeholder="🔍 Filtrar por patrimônio, descrição, sala...">
                    </div>
                </div>
            </div>
        </div>

        <!-- Tabela Principal: Relação Analítica de Bens -->
        <div class="card mb-4">
            <div class="card-header bg-white py-3 d-flex justify-content-between align-items-center">
                <h6 class="mb-0 fw-bold">📋 Relação Patrimonial Conferida</h6>
                <span class="badge bg-light text-dark border">{{ relatorio.bens|length }} bens auditados</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0" id="tabelaConferencia">
                        <thead class="table-light small">
                            <tr>
                                <th>Patrimônio</th>
                                <th>Descrição do Bem</th>
                                <th>Tag RFID (EPC)</th>
                                <th>Sala Esperada</th>
                                <th>Sala(s) Onde Foi Lido</th>
                                <th>Situação da Auditoria</th>
                                <th>Diagnóstico / Ação</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for b in relatorio.bens %}
                            <tr data-situacao="{{ b.situacao }}">
                                <td class="fw-bold font-monospace">{{ b.patrimonio }}</td>
                                <td>{{ b.descricao }}</td>
                                <td class="font-monospace small">
                                    {% if b.epc %}
                                    <span class="badge bg-light text-dark border">{{ b.epc }}</span>
                                    {% else %}
                                    <span class="text-muted fst-italic">Sem tag</span>
                                    {% endif %}
                                </td>
                                <td>
                                    <span class="badge bg-light text-primary border">{{ b.sala_esperada or 'Sem sala' }}</span>
                                </td>
                                <td>
                                    {% if b.salas_lidas %}
                                        {% for sl in b.salas_lidas %}
                                        <span class="badge {% if sl == b.sala_esperada %}bg-success-subtle text-success border border-success{% else %}bg-warning-subtle text-dark border border-warning{% endif %}">
                                            {{ sl }}
                                        </span>
                                        {% endfor %}
                                    {% else %}
                                        <span class="text-muted fst-italic small">Não detectado</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if b.situacao == 'localizado' %}
                                        <span class="badge bg-success-subtle text-success border border-success">🟢 Localizado</span>
                                    {% elif b.situacao == 'fora do local' %}
                                        <span class="badge bg-warning-subtle text-dark border border-warning">🟡 Fora do Local</span>
                                    {% elif b.situacao == 'nao localizado' %}
                                        <span class="badge bg-danger-subtle text-danger border border-danger">🔴 Não Localizado</span>
                                    {% elif b.situacao == 'sem cobertura' %}
                                        <span class="badge bg-secondary-subtle text-secondary border border-secondary">⚪ Sem Cobertura</span>
                                    {% else %}
                                        <span class="badge bg-light text-dark border">{{ b.situacao }}</span>
                                    {% endif %}
                                </td>
                                <td class="small">
                                    {% if b.situacao == 'localizado' %}
                                        {% if b.salas_lidas|length > 1 %}
                                        <span class="text-success fw-semibold">Item confirmado na sala esperada</span> <span class="text-muted">(também detectado em outra sala)</span>
                                        {% else %}
                                        <span class="text-success fw-semibold">Item confirmado no local correto.</span>
                                        {% endif %}
                                    {% elif b.situacao == 'fora do local' %}
                                        <span class="text-warning-emphasis fw-bold">Transferência não autorizada:</span> esperado em {{ b.sala_esperada }}, mas lido em {{ b.salas_lidas|join(', ') }}.
                                    {% elif b.situacao == 'nao localizado' %}
                                        <span class="text-danger fw-semibold">Faltante na sala {{ b.sala_esperada }}.</span> Verificar extravio ou leitura manual.
                                    {% elif b.situacao == 'sem cobertura' %}
                                        <span class="text-muted">A sala {{ b.sala_esperada }} ainda não foi varrida nesta sessão.</span>
                                    {% endif %}
                                </td>
                            </tr>
                            {% else %}
                            <tr>
                                <td colspan="7" class="text-center py-4 text-muted">
                                    Nenhum bem cadastrado para a unidade desta sessão.
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Tabela Secundária: Sobras / Tags Não Cadastradas no Ambiente -->
        {% if relatorio.epcs_nao_cadastrados %}
        <div class="card mb-4" id="secaoSobras">
            <div class="card-header bg-white py-3 d-flex justify-content-between align-items-center">
                <div class="d-flex align-items-center gap-2">
                    <span class="fs-5">🔵</span>
                    <div>
                        <h6 class="mb-0 fw-bold text-info-emphasis">Tags RFID Sobrando no Ambiente (Não Cadastradas)</h6>
                        <small class="text-muted">Etiquetas lidas pelo coletor que não possuem correspondência patrimonial no cadastro</small>
                    </div>
                </div>
                <span class="badge bg-info-subtle text-info-emphasis border border-info">{{ relatorio.epcs_nao_cadastrados|length }} tag(s)</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive" id="containerTabelaSobras" style="max-height: 380px; overflow-y: auto;">
                    <table class="table table-hover align-middle mb-0">
                        <thead class="table-light small">
                            <tr>
                                <th>EPC da Tag Detectada</th>
                                <th>Sala(s) Onde Foi Detectada</th>
                                <th>Situação</th>
                                <th>Ação Recomendada</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for item in relatorio.epcs_nao_cadastrados %}
                            <tr>
                                <td class="font-monospace small fw-bold">
                                    <span class="badge bg-light text-dark border">{{ item.epc }}</span>
                                </td>
                                <td>
                                    {% for s in item.salas_lidas %}
                                    <span class="badge bg-light text-dark border">{{ s }}</span>
                                    {% endfor %}
                                </td>
                                <td>
                                    <span class="badge bg-info-subtle text-info border border-info">🔵 Não Cadastrado</span>
                                </td>
                                <td class="small text-muted">
                                    Verificar se o bem pertence a outra unidade ou se foi etiquetado antes do cadastro patrimonial.
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>
        {% endif %}

        <!-- Bloco Oficial de Assinaturas (Aparece Apenas no PDF / Impressão) -->
        <div class="only-print mt-5 pt-4">
            <div class="row text-center">
                <div class="col-6">
                    <div style="border-top: 1px solid #000; width: 80%; margin: 0 auto; padding-top: 5px;">
                        <strong>{{ sessao_atual.operador or 'Operador de Coleta' }}</strong><br>
                        <span class="small text-muted">Responsável pela Coleta de Campo</span>
                    </div>
                </div>
                <div class="col-6">
                    <div style="border-top: 1px solid #000; width: 80%; margin: 0 auto; padding-top: 5px;">
                        <strong>Comissão de Inventário Patrimonial</strong><br>
                        <span class="small text-muted">Auditoria e Homologação</span>
                    </div>
                </div>
            </div>
        </div>

        {% else %}
        <!-- Estado Vazio / Sem Sessão Selecionada -->
        <div class="card p-5 text-center my-4">
            <div class="fs-1 mb-3">📋</div>
            <h5 class="fw-bold">Nenhuma sessão de inventário disponível para visualização</h5>
            <p class="text-muted small">
                Para visualizar a conferência, realize uma coleta com o Urovo DT50 ou utilize o quadro acima para importar os arquivos CSV offline.
            </p>
        </div>
        {% endif %}
    </div>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Toggle da área de importação
        function toggleAreaImportacao() {
            const card = document.getElementById('cardImportacao');
            if (card) {
                if (card.style.display === 'none') {
                    card.style.display = 'block';
                    card.scrollIntoView({ behavior: 'smooth' });
                } else {
                    card.style.display = 'none';
                }
            }
        }

        // Filtro interativo na tabela
        function filtrarTabela(situacao, btn) {
            const botoes = document.querySelectorAll('.btn-filtro');
            botoes.forEach(b => {
                b.className = 'btn btn-sm btn-outline-secondary btn-filtro';
            });
            if (btn) {
                btn.className = 'btn btn-sm btn-primary btn-filtro';
            }

            const linhas = document.querySelectorAll('#tabelaConferencia tbody tr');
            linhas.forEach(linha => {
                const sit = linha.getAttribute('data-situacao');
                if (situacao === 'todos' || sit === situacao) {
                    linha.style.display = '';
                } else {
                    linha.style.display = 'none';
                }
            });
        }

        function rolarParaSobras() {
            const secao = document.getElementById('secaoSobras');
            if (secao) {
                secao.scrollIntoView({ behavior: 'smooth' });
            }
        }

        // Alterna inclusao de sobras na impressao/PDF
        function toggleImprimirSobras(incluir) {
            const secao = document.getElementById('secaoSobras');
            if (!secao) return;
            if (incluir) {
                secao.classList.remove('no-print');
            } else {
                secao.classList.add('no-print');
            }
        }

        // Busca instantânea
        const inputBusca = document.getElementById('inputBuscaRelatorio');
        if (inputBusca) {
            inputBusca.addEventListener('keyup', function() {
                const termo = this.value.toLowerCase();
                const linhas = document.querySelectorAll('#tabelaConferencia tbody tr');
                linhas.forEach(linha => {
                    const texto = linha.innerText.toLowerCase();
                    linha.style.display = texto.includes(termo) ? '' : 'none';
                });
            });
        }

        // Preparação e Disparo para Impressão / PDF
        function imprimirRelatorio() {
            const agora = new Date();
            const dataFormatada = agora.toLocaleDateString('pt-BR') + ' ' + agora.toLocaleTimeString('pt-BR');
            document.querySelectorAll('.data-emissao-print').forEach(el => el.innerText = dataFormatada);
            window.print();
        }

        // Arraste e Solte de Arquivos (Drag and Drop Multi-File)
        const dropZone = document.getElementById('dropZone');
        const inputFile = document.getElementById('inputFileCsv');
        const fileListContainer = document.getElementById('fileListContainer');
        const btnContainer = document.getElementById('btnContainer');

        if (dropZone && inputFile) {
            dropZone.addEventListener('click', (e) => {
                if (e.target.id !== 'btnEnviarCsv' && !e.target.closest('#btnEnviarCsv')) {
                    inputFile.click();
                }
            });

            ['dragenter', 'dragover'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dropZone.classList.add('dragover');
                }, false);
            });

            ['dragleave', 'drop'].forEach(eventName => {
                dropZone.addEventListener(eventName, (e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    dropZone.classList.remove('dragover');
                }, false);
            });

            dropZone.addEventListener('drop', (e) => {
                const dt = e.dataTransfer;
                inputFile.files = dt.files;
                atualizarListaArquivos(dt.files);
            }, false);

            inputFile.addEventListener('change', () => {
                atualizarListaArquivos(inputFile.files);
            });

            function atualizarListaArquivos(files) {
                if (!files || files.length === 0) {
                    fileListContainer.classList.add('d-none');
                    btnContainer.classList.add('d-none');
                    return;
                }
                let html = '<div class="alert alert-info py-2 mb-0"><h6 class="mb-1 fw-bold">📄 ' + files.length + ' arquivo(s) CSV selecionado(s):</h6><ul class="mb-0 ps-3">';
                for (let i = 0; i < files.length; i++) {
                    html += '<li><code>' + files[i].name + '</code> (' + Math.round(files[i].size / 1024) + ' KB)</li>';
                }
                html += '</ul></div>';
                fileListContainer.innerHTML = html;
                fileListContainer.classList.remove('d-none');
                btnContainer.classList.remove('d-none');
            }
        }

        // Monitoramento da Conexão com o Urovo
        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        badge.className = 'badge bg-success p-2 d-flex align-items-center gap-2';
                        dot.className = 'rounded-circle bg-white spinner-grow spinner-grow-sm';
                        dot.style.width = '8px';
                        dot.style.height = '8px';
                        texto.innerText = `🟢 Coletor Conectado (${data.ip || 'Urovo'} · há ${data.segundos_atras}s)`;
                    } else {
                        badge.className = 'badge bg-secondary p-2 d-flex align-items-center gap-2';
                        dot.className = 'rounded-circle bg-white';
                        dot.style.width = '8px';
                        dot.style.height = '8px';
                        texto.innerText = '⚪ Coletor: Aguardando Wi-Fi';
                    }
                })
                .catch(() => {});
        }
        setInterval(atualizarStatusColetor, 3000);
        atualizarStatusColetor();
    </script>
</body>
</html>
"""


# ---------------------------------------------------------------- Rotas Web



@app.route("/")
def index():
    con = get_db()
    bens = con.execute(
        "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, s.nome AS sala "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "ORDER BY s.nome, b.patrimonio"
    ).fetchall()
    salas = con.execute("SELECT id, nome, unidade FROM sala ORDER BY nome").fetchall()
    ip_local = obter_ip_local()
    msg = request.args.get("msg")
    tipo_msg = request.args.get("tipo", "info")
    return render_template_string(
        HTML_TEMPLATE, bens=bens, salas=salas, ip_local=ip_local, mensagem=msg, tipo_msg=tipo_msg
    )


@app.route("/cadastrar", methods=["POST"])
def cadastrar_bem():
    patrimonio = request.form.get("patrimonio", "").strip()
    descricao = request.form.get("descricao", "").strip()
    epc = request.form.get("epc", "").strip().upper() or None
    sala_escolhida = request.form.get("sala", "").strip()
    metalico = 1 if request.form.get("metalico") else 0

    if not patrimonio or not descricao or not sala_escolhida:
        return redirect(url_for("index", msg="Patrimônio, descrição e sala são obrigatórios.", tipo="danger"))

    con = get_db()
    # O formulário manda o id da sala: o nome sozinho é ambíguo quando duas unidades
    # têm uma sala com o mesmo nome. O nome continua aceito para página aberta antes.
    if sala_escolhida.isdigit():
        sala = con.execute(
            "SELECT id, unidade FROM sala WHERE id = ?", (int(sala_escolhida),)
        ).fetchone()
    else:
        sala = con.execute(
            "SELECT id, unidade FROM sala WHERE LOWER(nome) = LOWER(?)",
            (sala_escolhida,),
        ).fetchone()
    if not sala:
        return redirect(url_for("index", msg=f"A sala '{sala_escolhida}' não foi encontrada.", tipo="danger"))

    sala_id = sala["id"]

    # Insere ou atualiza o bem
    try:
        con.execute(
            "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, epc, metalico, importado_em) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(patrimonio) DO UPDATE SET "
            "  descricao = excluded.descricao, "
            "  sala_esperada_id = excluded.sala_esperada_id, "
            "  epc = COALESCE(excluded.epc, bem.epc), "
            "  metalico = excluded.metalico",
            (
                patrimonio,
                descricao,
                sala_id,
                epc,
                metalico,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        con.commit()
    except sqlite3.IntegrityError as e:
        return redirect(url_for("index", msg=f"Erro ao salvar: {e}", tipo="danger"))

    return redirect(url_for("index", msg=f"Bem {patrimonio} salvo com sucesso!", tipo="success"))


@app.route("/excluir/<patrimonio>", methods=["POST"])
def excluir_bem(patrimonio):
    con = get_db()
    con.execute("DELETE FROM bem WHERE patrimonio = ?", (patrimonio,))
    con.commit()
    return redirect(url_for("index", msg=f"Bem {patrimonio} excluído.", tipo="warning"))


@app.route("/salas")
def listar_salas():
    """Página dedicada à gestão e cadastro de salas."""
    con = get_db()
    salas = con.execute(
        "SELECT s.id, s.nome, s.unidade, COUNT(b.patrimonio) AS total_bens "
        "FROM sala s LEFT JOIN bem b ON b.sala_esperada_id = s.id "
        "GROUP BY s.id, s.nome, s.unidade "
        "ORDER BY s.nome"
    ).fetchall()
    ip_local = obter_ip_local()
    msg = request.args.get("msg")
    tipo_msg = request.args.get("tipo", "info")
    return render_template_string(
        HTML_SALAS_TEMPLATE, salas=salas, ip_local=ip_local, mensagem=msg, tipo_msg=tipo_msg
    )


@app.route("/salas/cadastrar", methods=["POST"])
def cadastrar_sala():
    """Cadastra uma nova sala no banco de dados."""
    nome = request.form.get("nome", "").strip()
    unidade = request.form.get("unidade", "").strip() or "UFAL"
    if not nome:
        return redirect(url_for("listar_salas", msg="O nome da sala é obrigatório.", tipo="danger"))

    con = get_db()
    try:
        con.execute(
            "INSERT INTO sala (unidade, nome) VALUES (?, ?)",
            (unidade, nome),
        )
        con.commit()
    except sqlite3.IntegrityError:
        return redirect(url_for("listar_salas", msg=f"A sala '{nome}' já está cadastrada na unidade {unidade}.", tipo="warning"))

    return redirect(url_for("listar_salas", msg=f"Sala '{nome}' cadastrada com sucesso!", tipo="success"))


@app.route("/salas/excluir/<int:sala_id>", methods=["POST"])
def excluir_sala(sala_id):
    """Exclui uma sala caso não haja bens associados."""
    con = get_db()
    row = con.execute("SELECT COUNT(*) FROM bem WHERE sala_esperada_id = ?", (sala_id,)).fetchone()
    bens_vinculados = row[0] if row else 0
    if bens_vinculados > 0:
        return redirect(url_for("listar_salas", msg=f"Não é possível excluir: existem {bens_vinculados} bens vinculados a esta sala.", tipo="danger"))

    con.execute("DELETE FROM sala WHERE id = ?", (sala_id,))
    con.commit()
    return redirect(url_for("listar_salas", msg="Sala excluída com sucesso.", tipo="warning"))


@app.route("/relatorio")
def ver_relatorio():
    """Visualização da conferência e auditoria patrimonial (RF07 / RF08)."""
    con = get_db()
    sessoes = con.execute(
        "SELECT s.id, s.unidade, s.operador, s.inicio, s.fim, s.duracao_s, s.n_varreduras, "
        "       COUNT(v.id) AS total_varreduras "
        "FROM sessao s LEFT JOIN varredura v ON v.sessao_id = s.id "
        "GROUP BY s.id ORDER BY s.id DESC"
    ).fetchall()

    sessao_id = request.args.get("sessao", "").strip()
    if not sessao_id and sessoes:
        sessao_id = sessoes[0]["id"]

    sessao_atual = None
    varreduras = []
    relatorio = None
    localizados = []
    fora_do_local = []
    nao_localizados = []
    sem_cobertura = []
    nao_cadastrados = []
    pct_localizados = 0.0

    if sessao_id:
        sessao_atual = con.execute("SELECT * FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        varreduras = con.execute(
            "SELECT v.numero, v.inicio, v.fim, v.duracao_s, v.linhas_brutas, s.nome AS sala, "
            "       COUNT(l.epc) AS total_etiquetas "
            "FROM varredura v "
            "LEFT JOIN sala s ON s.id = v.sala_id "
            "LEFT JOIN leitura l ON l.varredura_id = v.id "
            "WHERE v.sessao_id = ? "
            "GROUP BY v.id ORDER BY v.numero",
            (sessao_id,),
        ).fetchall()

        try:
            relatorio = conferencia.conferir_sessao(con, sessao_id)
            for b in relatorio.bens:
                if b.situacao == conferencia.LOCALIZADO:
                    localizados.append(b)
                elif b.situacao == conferencia.FORA_DO_LOCAL:
                    fora_do_local.append(b)
                elif b.situacao == conferencia.NAO_LOCALIZADO:
                    nao_localizados.append(b)
                elif b.situacao == conferencia.SEM_COBERTURA:
                    sem_cobertura.append(b)

            nao_cadastrados = relatorio.epcs_nao_cadastrados
            if relatorio.bens:
                pct_localizados = round((len(localizados) / len(relatorio.bens)) * 100, 1)
        except conferencia.SessaoNaoEncontrada:
            relatorio = None

    ip_local = obter_ip_local()
    msg = request.args.get("msg")
    tipo_msg = request.args.get("tipo", "info")

    return render_template_string(
        HTML_RELATORIO_TEMPLATE,
        sessoes=sessoes,
        sessao_atual=sessao_atual,
        sessao_id=sessao_id,
        varreduras=varreduras,
        relatorio=relatorio,
        localizados=localizados,
        fora_do_local=fora_do_local,
        nao_localizados=nao_localizados,
        sem_cobertura=sem_cobertura,
        nao_cadastrados=nao_cadastrados,
        pct_localizados=pct_localizados,
        ip_local=ip_local,
        mensagem=msg,
        tipo_msg=tipo_msg,
    )


@app.route("/relatorio/importar-csv", methods=["POST"])
def importar_csv_offline():
    """Recebe múltiplos arquivos CSV exportados pelo DT50 em modo offline."""
    arquivos = request.files.getlist("arquivos")
    if not arquivos or all(not f.filename for f in arquivos):
        return redirect(url_for("ver_relatorio", msg="Nenhum arquivo CSV foi selecionado.", tipo="warning"))

    con = get_db()
    temp_dir = Path(tempfile.gettempdir()) / "fiea_csv_uploads"
    temp_dir.mkdir(parents=True, exist_ok=True)

    arquivos_importados = 0
    erros = []
    sessao_id_detectada = None

    for f in arquivos:
        if not f or not f.filename:
            continue
        nome = f.filename.lower()
        if not nome.endswith(".csv"):
            continue

        temp_path = temp_dir / Path(f.filename).name
        f.save(str(temp_path))

        try:
            resultado = importador.importar_arquivo(temp_path, con)
            arquivos_importados += 1
            if resultado.tipo == "sessao":
                sessao_id_detectada = resultado.identificador
            elif resultado.tipo == "varredura" and not sessao_id_detectada:
                if "-" in resultado.identificador:
                    sessao_id_detectada = resultado.identificador.split("-")[0]
        except Exception as e:
            erros.append(f"{f.filename}: {e}")
        finally:
            try:
                temp_path.unlink()
            except Exception:
                pass

    if arquivos_importados == 0 and erros:
        return redirect(url_for("ver_relatorio", msg=f"Erro ao importar arquivos: {'; '.join(erros)}", tipo="danger"))

    msg = f"Sucesso! {arquivos_importados} arquivo(s) CSV processado(s) e incorporado(s) à base de dados."
    if erros:
        msg += f" (Avisos: {'; '.join(erros)})"

    url_params = {"msg": msg, "tipo": "success"}
    if sessao_id_detectada:
        url_params["sessao"] = sessao_id_detectada

    return redirect(url_for("ver_relatorio", **url_params))


@app.route("/relatorio/<sessao_id>/exportar-csv")
def exportar_relatorio_csv(sessao_id):
    """Gera um arquivo CSV/Excel compatível com formato pt-BR (ponto-e-vírgula e UTF-8 BOM)."""
    con = get_db()
    try:
        rel = conferencia.conferir_sessao(con, sessao_id)
    except conferencia.SessaoNaoEncontrada:
        return redirect(url_for("ver_relatorio", msg=f"Sessão {sessao_id} não encontrada.", tipo="danger"))

    output = io.StringIO()
    # Grava BOM para o Excel no Windows abrir com acentuação e caracteres especiais perfeitos
    output.write("\ufeff")
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "Patrimonio", "Descricao", "EPC", "Sala_Esperada", "Salas_Lidas",
        "Situacao", "Unidade", "Sessao_ID", "Diagnostico"
    ])

    for b in rel.bens:
        salas_lidas_str = ", ".join(b.salas_lidas) if b.salas_lidas else "Nenhuma"
        if b.situacao == conferencia.LOCALIZADO:
            obs = "Item confirmado na sala correta"
            if any(s != b.sala_esperada for s in b.salas_lidas):
                obs += f" (também detectado em outra sala: {salas_lidas_str})"
        elif b.situacao == conferencia.FORA_DO_LOCAL:
            obs = f"Transferência indevida: esperado em '{b.sala_esperada}', mas detectado em '{salas_lidas_str}'"
        elif b.situacao == conferencia.NAO_LOCALIZADO:
            obs = f"Faltante: não detectado na sala '{b.sala_esperada}' durante a varredura"
        elif b.situacao == conferencia.SEM_COBERTURA:
            obs = f"Sem cobertura: a sala '{b.sala_esperada}' não foi varrida nesta sessão"
        else:
            obs = ""

        writer.writerow([
            b.patrimonio,
            b.descricao,
            b.epc or "",
            b.sala_esperada or "",
            salas_lidas_str,
            b.situacao.upper(),
            rel.unidade,
            rel.sessao_id,
            obs,
        ])

    for item in rel.epcs_nao_cadastrados:
        writer.writerow([
            "N/A",
            "Tag RFID Sobrando no Ambiente (Não Cadastrada)",
            item.epc,
            "N/A",
            ", ".join(item.salas_lidas),
            "NAO CADASTRADO",
            rel.unidade,
            rel.sessao_id,
            "Etiqueta RFID lida pelo coletor sem correspondência no cadastro de bens",
        ])

    csv_data = output.getvalue()
    response = make_response(csv_data)
    response.headers["Content-Disposition"] = f"attachment; filename=relatorio_inventario_{sessao_id}.csv"
    response.headers["Content-Type"] = "text/csv; charset=utf-8-sig"
    return response


@app.route("/relatorio/excluir-sessao/<sessao_id>", methods=["POST"])
def excluir_sessao(sessao_id):
    """Exclui uma sessão de inventário e todas as suas leituras/varreduras do banco."""
    con = get_db()
    varreduras = con.execute("SELECT id FROM varredura WHERE sessao_id = ?", (sessao_id,)).fetchall()
    for v in varreduras:
        con.execute("DELETE FROM leitura WHERE varredura_id = ?", (v["id"],))
    con.execute("DELETE FROM varredura WHERE sessao_id = ?", (sessao_id,))
    con.execute("DELETE FROM sessao WHERE id = ?", (sessao_id,))
    con.commit()
    return redirect(url_for("ver_relatorio", msg=f"Sessão {sessao_id} excluída com sucesso do histórico.", tipo="warning"))


# ---------------------------------------------------------------- Endpoints REST para o Urovo


@app.route("/api/status", methods=["GET"])
def api_status():
    """Health-check para o Urovo detectar que o servidor está online."""
    registrar_contato_coletor("health-check")
    return jsonify({
        "status": "online",
        "servidor": "FIEA Coleta Local",
        "ip": obter_ip_local(),
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/status-coletor", methods=["GET"])
def api_status_coletor():
    """Consulta para o dashboard web saber se o Urovo está conectado."""
    ts = ultimo_contato_coletor.get("timestamp")
    conectado = bool(ts and (time.time() - ts < 45))
    segundos = int(time.time() - ts) if ts else None
    return jsonify({
        "conectado": conectado,
        "ip": ultimo_contato_coletor.get("ip"),
        "acao": ultimo_contato_coletor.get("acao"),
        "segundos_atras": segundos,
    })


@app.route("/api/tag-capturada", methods=["POST"])
def api_salvar_tag_capturada():
    """Recebe do Urovo o EPC lido por proximidade (baixa potência) para preencher a tela web."""
    registrar_contato_coletor("tag-capturada")
    dados = request.get_json(force=True, silent=True) or {}
    epc = dados.get("epc", "").strip().upper()
    if not epc:
        return jsonify({"erro": "epc é obrigatório"}), 400

    global tag_capturada_recente
    tag_capturada_recente = {"epc": epc, "timestamp": time.time()}
    return jsonify({"status": "sucesso", "epc": epc})


@app.route("/api/tag-capturada", methods=["GET"])
def api_obter_tag_capturada():
    """Retorna para a página web o último EPC capturado pelo Urovo (ativo por até 30s)."""
    global tag_capturada_recente
    if tag_capturada_recente["epc"] and (time.time() - tag_capturada_recente["timestamp"] < 30):
        epc = tag_capturada_recente["epc"]
        # Limpa para evitar reprocessamento involuntário
        tag_capturada_recente = {"epc": None, "timestamp": 0}
        return jsonify({"epc": epc})
    return jsonify({"epc": None})


@app.route("/api/salas", methods=["GET"])
def api_salas():
    """Lista todas as salas cadastradas."""
    registrar_contato_coletor("consulta-salas")
    con = get_db()
    salas = con.execute("SELECT id, nome, unidade FROM sala ORDER BY nome").fetchall()
    return jsonify([{"id": s["id"], "nome": s["nome"], "unidade": s["unidade"]} for s in salas])


@app.route("/api/bens", methods=["GET"])
def api_bens():
    """Retorna os bens esperados. Pode ser filtrado por ?sala=EASY e ?unidade=UFAL."""
    registrar_contato_coletor("consulta-bens")
    sala_filtro = request.args.get("sala", "").strip()
    unidade_filtro = request.args.get("unidade", "").strip()
    con = get_db()
    # Com uma unidade cadastrada, só as salas dela: duas unidades podem ter uma sala
    # "Almoxarifado". Unidade desconhecida (digitada no coletor) cai no filtro antigo,
    # só pelo nome, para o coletor não ficar sem lista de bens.
    unidade_conhecida = bool(unidade_filtro) and con.execute(
        "SELECT 1 FROM sala WHERE unidade = ? LIMIT 1", (unidade_filtro,)
    ).fetchone() is not None
    if sala_filtro and unidade_conhecida:
        bens = con.execute(
            "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, s.nome AS sala, s.unidade "
            "FROM bem b JOIN sala s ON s.id = b.sala_esperada_id "
            "WHERE LOWER(s.nome) = LOWER(?) AND s.unidade = ? "
            "ORDER BY b.patrimonio",
            (sala_filtro, unidade_filtro),
        ).fetchall()
    elif sala_filtro:
        bens = con.execute(
            "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, s.nome AS sala, s.unidade "
            "FROM bem b JOIN sala s ON s.id = b.sala_esperada_id "
            "WHERE LOWER(s.nome) = LOWER(?) "
            "ORDER BY b.patrimonio",
            (sala_filtro,),
        ).fetchall()
    else:
        bens = con.execute(
            "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, s.nome AS sala, s.unidade "
            "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
            "ORDER BY s.nome, b.patrimonio"
        ).fetchall()

    return jsonify([
        {
            "patrimonio": b["patrimonio"],
            "descricao": b["descricao"] or "",
            "epc": b["epc"] or "",
            "metalico": bool(b["metalico"]),
            "sala": b["sala"] or "",
            "unidade": b["unidade"] or "",
        }
        for b in bens
    ])


@app.route("/api/sincronizar", methods=["POST"])
def api_sincronizar():
    """Recebe do Urovo uma varredura concluída com suas leituras e pendências resolvidas.

    Payload esperado:
    {
      "sessao": {"id": "...", "unidade": "...", "operador": "...", "inicio": "...", "fim": "...", "duracao_s": 20},
      "varredura": {"id": "...", "sala": "...", "tecnica": "...", "potencia_dbm": 30, "inicio": "...", "fim": "...", "duracao_s": 20},
      "leituras": [{"epc": "...", "metodo": "RFID", "leituras": 10, "rssi_min": 60, "rssi_medio": 65.0, "rssi_max": 70, "primeira_leitura": "...", "ultima_leitura": "..."}],
      "pendencias": [{"patrimonio": "...", "epc": "...", "metodo": "QR", "timestamp": "..."}]
    }
    """
    dados = request.get_json(force=True, silent=True)
    if not dados:
        return jsonify({"erro": "JSON inválido ou vazio"}), 400

    registrar_contato_coletor("sincronizacao")

    sessao_info = dados.get("sessao", {})
    varredura_info = dados.get("varredura", {})
    leituras = dados.get("leituras", [])
    pendencias = dados.get("pendencias", [])

    con = get_db()

    # 1. Atualiza/Cria Sessão
    if sessao_info.get("id"):
        con.execute(
            "INSERT INTO sessao (id, unidade, operador, inicio, fim, duracao_s, n_varreduras) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  unidade = excluded.unidade, operador = excluded.operador, "
            "  fim = COALESCE(excluded.fim, sessao.fim), "
            "  duracao_s = COALESCE(excluded.duracao_s, sessao.duracao_s), "
            "  n_varreduras = COALESCE(excluded.n_varreduras, sessao.n_varreduras)",
            (
                sessao_info["id"],
                sessao_info.get("unidade", "UFAL"),
                sessao_info.get("operador", "Operador"),
                sessao_info.get("inicio"),
                sessao_info.get("fim"),
                sessao_info.get("duracao_s"),
                sessao_info.get("n_varreduras", 1),
            ),
        )

    # 2. Sala da varredura
    sala_nome = varredura_info.get("sala", "").strip()
    unidade = sessao_info.get("unidade", "UFAL")
    sala_id = None
    if sala_nome:
        con.execute(
            "INSERT INTO sala (unidade, nome) VALUES (?, ?) ON CONFLICT(unidade, nome) DO NOTHING",
            (unidade, sala_nome),
        )
        s_row = con.execute(
            "SELECT id FROM sala WHERE LOWER(nome) = LOWER(?) AND unidade = ?",
            (sala_nome, unidade),
        ).fetchone()
        if s_row:
            sala_id = s_row["id"]

    # 3. Cria/Atualiza Varredura
    varredura_id = varredura_info.get("id")
    if not varredura_id:
        return jsonify({"erro": "varredura.id é obrigatório"}), 400

    numero = 1
    if "-v" in varredura_id:
        sufixo = varredura_id.rsplit("-v", 1)[-1]
        if sufixo.isdigit():
            numero = int(sufixo)

    total_leituras = len(leituras) + len(pendencias)
    con.execute(
        "INSERT INTO varredura (id, sessao_id, numero, sala_id, tecnica, potencia_dbm, "
        "inicio, fim, duracao_s, arquivo_origem, importado_em, linhas_brutas) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "  sala_id = excluded.sala_id, tecnica = excluded.tecnica, "
        "  potencia_dbm = excluded.potencia_dbm, inicio = excluded.inicio, "
        "  fim = excluded.fim, duracao_s = excluded.duracao_s, "
        "  importado_em = excluded.importado_em, linhas_brutas = excluded.linhas_brutas",
        (
            varredura_id,
            sessao_info.get("id", varredura_id.split("-v")[0]),
            numero,
            sala_id,
            varredura_info.get("tecnica", "estatica_porta"),
            varredura_info.get("potencia_dbm", 30),
            varredura_info.get("inicio", datetime.now().isoformat()),
            varredura_info.get("fim"),
            varredura_info.get("duracao_s", 0),
            "sincronizacao_wifi",
            datetime.now().isoformat(timespec="seconds"),
            total_leituras,
        ),
    )

    # 4. Grava Leituras RFID (deduplicadas)
    con.execute("DELETE FROM leitura WHERE varredura_id = ?", (varredura_id,))
    for l in leituras:
        epc = l.get("epc", "").strip().upper()
        if not epc:
            continue
        con.execute(
            "INSERT INTO leitura (varredura_id, epc, metodo, leituras, rssi_min, "
            "rssi_medio, rssi_max, primeira_leitura, ultima_leitura) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(varredura_id, epc, metodo) DO UPDATE SET "
            "  leituras = excluded.leituras, rssi_medio = excluded.rssi_medio",
            (
                varredura_id,
                epc,
                l.get("metodo", "RFID"),
                l.get("leituras", 1),
                l.get("rssi_min", 0),
                l.get("rssi_medio", 0.0),
                l.get("rssi_max", 0),
                l.get("primeira_leitura", datetime.now().isoformat()),
                l.get("ultima_leitura", datetime.now().isoformat()),
            ),
        )

    # 5. Grava Resolução de Pendências (QR Code, Manual ou Faltante - RF10)
    for p in pendencias:
        metodo = p.get("metodo", "MANUAL").upper()
        epc = p.get("epc", "").strip().upper()
        patrimonio = p.get("patrimonio", "").strip()

        # Se o EPC não veio direto, procura se o bem já tinha EPC cadastrado
        if not epc and patrimonio:
            b_row = con.execute("SELECT epc FROM bem WHERE patrimonio = ?", (patrimonio,)).fetchone()
            if b_row and b_row["epc"]:
                epc = b_row["epc"]
            else:
                epc = f"PAT_{patrimonio}"

        agora = p.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")
        con.execute(
            "INSERT INTO leitura (varredura_id, epc, metodo, leituras, rssi_min, "
            "rssi_medio, rssi_max, primeira_leitura, ultima_leitura) "
            "VALUES (?, ?, ?, 1, 0, 0, 0, ?, ?) "
            "ON CONFLICT(varredura_id, epc, metodo) DO NOTHING",
            (varredura_id, epc, metodo, agora, agora),
        )

    con.commit()
    return jsonify({
        "status": "sucesso",
        "varredura_id": varredura_id,
        "leituras_gravadas": len(leituras),
        "pendencias_gravadas": len(pendencias),
    })


def iniciar_servidor(porta=5000):
    ip = obter_ip_local()
    print("=" * 65)
    print("🚀 SERVIDOR LOCAL DE INVENTÁRIO INICIADO COM SUCESSO!")
    print(f"👉 Interface Web no PC: http://localhost:{porta}")
    print(f"👉 Conexão Wi-Fi (Urovo DT50): http://{ip}:{porta}")
    print("=" * 65)
    app.run(host="0.0.0.0", port=porta, debug=False)


if __name__ == "__main__":
    iniciar_servidor()
