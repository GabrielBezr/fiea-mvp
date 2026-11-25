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
ultimo_contato_coletor = {"timestamp": None, "ip": None, "acao": None, "fila_pendente": 0}


def registrar_contato_coletor(acao="comunicacao", fila=None):
    global ultimo_contato_coletor
    fila_val = ultimo_contato_coletor.get("fila_pendente", 0)
    if fila is not None:
        try:
            fila_val = max(0, int(fila))
        except (ValueError, TypeError):
            pass
    ultimo_contato_coletor = {
        "timestamp": time.time(),
        "ip": request.remote_addr,
        "acao": acao,
        "fila_pendente": fila_val,
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


def gerar_token_conexao(ip):
    """Gera um token curto de conexão a partir do IP local (ex: 192.168.1.150 -> C0A80196)."""
    try:
        partes = [int(p) for p in ip.split(".")]
        if len(partes) == 4:
            return f"{partes[0]:02X}{partes[1]:02X}{partes[2]:02X}{partes[3]:02X}"
    except Exception:
        pass
    return "00000000"

import base64

FAVICON_ICO_BYTES = base64.b64decode("AAABAAMAEBAAAAEAIACqAQAANgAAACAgAAABACAABAMAAOABAAAwMAAAAQAgAPoEAADkBAAAiVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABcUlEQVR42mNgYGbjYtBLnsngNf8Lg9+y/0RhkFqQHpBeMINYjegYpJckm7G5hGzNMEw1A6zLd/3PmHbqf+qUk/8Zozf8Zyg49J/Bf/l/p+q9/zOh4iB1mllb/qdPPfVfL28bwgCz4h3///3793//pRf/1x579J8hc89/kYZT/93q9oHFd5579n/Orjv/WQOX/3/5/vv/bz///D9w+QXCgPpll8AKZ2y//T9l8on/NUsuAhW8/D9p843/n7//AotH9x79H9t37P/7Lz//z9t99/+Z228RBizef+//8Ruv/+fPPvPfoWrP/3N33wI13QLbfPTaK7A4yJXHrr/+//Tt1//n7777f/H+O4QBd59/+t+97hpYgC1oxf9fv/+C/X3x/vv/Vx99ALsgsvso2JVViy7833Di8f8X778hDAAFkAYwcEBs4eg1YL543Lr/urlb/6dNPQnmuwLDAxaQ+sAATJ50gsrRSL4BFCdlijMThdkZAKhUN7Xjk+ohAAAAAElFTkSuQmCCiVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACy0lEQVR42u1X7UtTYRS/JVgL+lIfoj4U1Mc+9Af0oaToBXpHShalIBlUWF8KelMpi0KpoKiMQiOxZbJJmJVvm+415wtJbbaZuc1pbZPNuTXny073nMu9LPdSH5q3YAcO9z7P+e2c3/O757l3D8PwlpG5hFm78wyzsaid2VMDKXHMjTWw1i8mWb6aySozpazwXMdaWFNY+XwWjyZBSqAk812cd6yd0mf+Jz0hWnHe0wTSBP5JAvuvd4CsYwgM/W7QmV3Q2OWEI7d0wEgVwJR+4PxwPWFzyjTwUmMjrJ71N91O2HVFJeTKva2DBuMw6M1uqFZ9hWXSuuQEdpQoIRKJwFw78aATmNPtwFSNAlPeD0yOHA7eVMdgcZx/10C5Nl9oEeL89Vrtx+QEFHo7Ab2BMBTcew/HWMfrqjwFR+B4E2y93AoLWGy9wUFYpydIGB6/Jp9Tp07L5TI7fGB3Bej+/mtLcgKB0BQBUa7F2S/IFx2QUSz7hho+2XygMbkgk53z/+Cw+AgkLE4ShV1xVA4ToWmKl9T0QffAGN1XvLUmJoAJoiXj/fPwOK3Y4Q5S7HHTAKw/2RAXa7R4KNepCqMwtzJXDkarh7APkxHYdL457vPHgusKXgnjYnZFWRdb4mJROcxlHfHT2MZKv/uqCixO/+8VOFfVK6xoQ2GjIGvGvufU7fyKthW1wdnKnoRYHMdTB+1p62BiAsgO7bs3RJJHxwofdQkJlx6qpW2KNjsbgbw7epCWa8lxmz1hFUMLTk5DW9838vEg1y9ynT0xgXc9IwQyWsdi3g2VLV8oFgrP0LiU3U7RW4/3LZdawTsRpnl8fyAWF8P3QHPvaGICWra7TXYflMlNMQRwy2HsmZKTEFVAxVCtyakZ2radbANuL24jHDr2Cf97mXqI5qqVg3//Vbxwb/pjlCbwXxMQ/W+56AcT0Y9moh9ORT6e/wQsXsMsZqQz6gAAAABJRU5ErkJggolQTkcNChoKAAAADUlIRFIAAAAwAAAAMAgGAAAAVwL5hwAABMFJREFUeNrtWmlMVFcUfi61i40xbW1S0+oPq1htsYs2qVtitNFoXQC1WhdaTCjVYoe6YKJxr00NajVqFIxAWnGAIgSQVbbKUhEHFAuGZRChgAIiyA7D6T13eI97Z97wpjVGnnk3OWHmzrnnfN89y7vkPkGQG6Mc5wvTdGHCvBN3hSVB8EwFMSAWxKQ4Xh09UZh9MOeZg7YliA0xyo7XHGYJC/2bByx4URAjYrXaeTWAZ0lwkRjIadNfOkkFqzbwotDCxgpXKwHEPiBa5ZO0WNWCF0UjoBHQCGgENAJPncDra/4AR8/LMO3HOJiyOQbe/TYS3lgbBkOW9uosuwiCS0if4Hdm/SAio13D6dpPt8TBhz/EwHiPSHhzXRgMXirv8y3XS1RvCpGRq0P/H4GvfDLgVlkD9PT0yMrpmCKz7r5cEALv98mWdDo/hBD5+ngWlFQ32bThde4G5/OzbfGQZ3zI6XSbesD/Sul/IzCV7HZrexfYGmjYO4AAX0z0fe6AEFANgn8VCH7/gOAWS22sP5apaMP55z8lny846Sl4udFtMsEHJAvsIoAhzyqstXJmMvHykc4MVPCtBOEcAa4jYJyCKSm0Yax5zK+XsTGepKPoF6OBeuwa9jNmhF0EJm+K5hbfq22mNTDOPVKSsRsievNfD4JnKs396dsTYLNvDgU/ycLG1b8fwPvfX6brRHn7mwjOr6G0vm/Hu03gc6lAsoF/N525bh+BFb9c5RaGZd7rt8gR+JnYIpou5xNL6JzOYjf36/P7tYGRwIiIIyq7EtxO/MXh2OZvsI/AnqBbXOhulz+CgKRSCEwySrLkYBrVRWBswW09b6ARCM+q4GykFzzg1qO9+XuSJZ9+8cUc2EX7U2Hd0UxubveFm8oE0HlQWplV/luKGM6km9WcHjrG+cKKRkUbYgGPIi25raNb0q1raoeXXPSwmuS8GBXUPxR6274IZBfVKToXd6/8frOkh0Xq4BEFw5z10NrRZWXD0tYYN3MNuB7jd/q3ZCPdyC8Pp3MEjkYUKhPA3v24tVNyXNPQBt6BubCDEczF4StDYMSqUC5vEfRQst7huygu/2sb23rX5kk2PM+aix19JubxUaxrbIeKuhaoJ5FgiZ2NK1YmgAXJOo++Xik5shR8qrK6hpJ6Or/spzRuPt5QZbN4P/GKhc4uEygNGpkUozIBLELW+fHIOzadrzmSwe3Qr0QXyer8bnDzv6eU2bThG1ds1e9ZYUdoerkyAWyDrHOP09k2nR8IzuccOx0yF+V28oS23L2mlk5olKQD9pJO9yKpFfzO6n1BmsAEUkco7ievcSmK2aBIIIO0O9bgnJ1XbBJIza/hdN/bGN17nkmgBd1fOrifugarSJGyu5yQWwWDmMPdgr0pHAHseIoEahpauRC+4xYuC/7l5cHQQh5coh7m8StkjrZiAsInvIC2RrkOhoezubuSIKe4npu3PCp8vjuZEhB/x+ONIgE8Dc7akUhFOuvICHarmd4Jku7HXta6eFzGdottcgN5qmLNLD6QCo7kWI3dagazfiaRoU78MXz4ihDpdxQ8imj/kWkENAIaAY2ARoAnoPoLDtVfMan+kk/116zPxUW36l81eC5e9lDp6zb/Ag5HzkaS2AxbAAAAAElFTkSuQmCC")
FAVICON_PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACy0lEQVR42u1X7UtTYRS/JVgL+lIfoj4U1Mc+9Af0oaToBXpHShalIBlUWF8KelMpi0KpoKiMQiOxZbJJmJVvm+415wtJbbaZuc1pbZPNuTXny073nMu9LPdSH5q3YAcO9z7P+e2c3/O757l3D8PwlpG5hFm78wyzsaid2VMDKXHMjTWw1i8mWb6aySozpazwXMdaWFNY+XwWjyZBSqAk812cd6yd0mf+Jz0hWnHe0wTSBP5JAvuvd4CsYwgM/W7QmV3Q2OWEI7d0wEgVwJR+4PxwPWFzyjTwUmMjrJ71N91O2HVFJeTKva2DBuMw6M1uqFZ9hWXSuuQEdpQoIRKJwFw78aATmNPtwFSNAlPeD0yOHA7eVMdgcZx/10C5Nl9oEeL89Vrtx+QEFHo7Ab2BMBTcew/HWMfrqjwFR+B4E2y93AoLWGy9wUFYpydIGB6/Jp9Tp07L5TI7fGB3Bej+/mtLcgKB0BQBUa7F2S/IFx2QUSz7hho+2XygMbkgk53z/+Cw+AgkLE4ShV1xVA4ToWmKl9T0QffAGN1XvLUmJoAJoiXj/fPwOK3Y4Q5S7HHTAKw/2RAXa7R4KNepCqMwtzJXDkarh7APkxHYdL457vPHgusKXgnjYnZFWRdb4mJROcxlHfHT2MZKv/uqCixO/+8VOFfVK6xoQ2GjIGvGvufU7fyKthW1wdnKnoRYHMdTB+1p62BiAsgO7bs3RJJHxwofdQkJlx6qpW2KNjsbgbw7epCWa8lxmz1hFUMLTk5DW9838vEg1y9ynT0xgXc9IwQyWsdi3g2VLV8oFgrP0LiU3U7RW4/3LZdawTsRpnl8fyAWF8P3QHPvaGICWra7TXYflMlNMQRwy2HsmZKTEFVAxVCtyakZ2radbANuL24jHDr2Cf97mXqI5qqVg3//Vbxwb/pjlCbwXxMQ/W+56AcT0Y9moh9ORT6e/wQsXsMsZqQz6gAAAABJRU5ErkJggg==")

HTML_HOME_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FIEA Inventário RFID — Painel de Controle</title>
    <link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACy0lEQVR42u1X7UtTYRS/JVgL+lIfoj4U1Mc+9Af0oaToBXpHShalIBlUWF8KelMpi0KpoKiMQiOxZbJJmJVvm+415wtJbbaZuc1pbZPNuTXny073nMu9LPdSH5q3YAcO9z7P+e2c3/O757l3D8PwlpG5hFm78wyzsaid2VMDKXHMjTWw1i8mWb6aySozpazwXMdaWFNY+XwWjyZBSqAk812cd6yd0mf+Jz0hWnHe0wTSBP5JAvuvd4CsYwgM/W7QmV3Q2OWEI7d0wEgVwJR+4PxwPWFzyjTwUmMjrJ71N91O2HVFJeTKva2DBuMw6M1uqFZ9hWXSuuQEdpQoIRKJwFw78aATmNPtwFSNAlPeD0yOHA7eVMdgcZx/10C5Nl9oEeL89Vrtx+QEFHo7Ab2BMBTcew/HWMfrqjwFR+B4E2y93AoLWGy9wUFYpydIGB6/Jp9Tp07L5TI7fGB3Bej+/mtLcgKB0BQBUa7F2S/IFx2QUSz7hho+2XygMbkgk53z/+Cw+AgkLE4ShV1xVA4ToWmKl9T0QffAGN1XvLUmJoAJoiXj/fPwOK3Y4Q5S7HHTAKw/2RAXa7R4KNepCqMwtzJXDkarh7APkxHYdL457vPHgusKXgnjYnZFWRdb4mJROcxlHfHT2MZKv/uqCixO/+8VOFfVK6xoQ2GjIGvGvufU7fyKthW1wdnKnoRYHMdTB+1p62BiAsgO7bs3RJJHxwofdQkJlx6qpW2KNjsbgbw7epCWa8lxmz1hFUMLTk5DW9838vEg1y9ynT0xgXc9IwQyWsdi3g2VLV8oFgrP0LiU3U7RW4/3LZdawTsRpnl8fyAWF8P3QHPvaGICWra7TXYflMlNMQRwy2HsmZKTEFVAxVCtyakZ2radbANuL24jHDr2Cf97mXqI5qqVg3//Vbxwb/pjlCbwXxMQ/W+56AcT0Y9moh9ORT6e/wQsXsMsZqQz6gAAAABJRU5ErkJggg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon.png?v=fiea">
    <link rel="icon" type="image/x-icon" href="/favicon.ico?v=fiea">
    <link rel="shortcut icon" href="/favicon.ico?v=fiea">
    <link rel="apple-touch-icon" href="/favicon.png?v=fiea">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root {
            --fiea-azul: #004EA6;
            --fiea-azul-escuro: #003A7C;
            --fiea-azul-claro: #00A0E7;
            --fiea-azul-fundo: #E6EEF8;
            --fundo: #F0F0EB;
            --superficie: #FAFAFA;
            --contorno: #E2E8F0;
            --contorno-forte: #CBD5E1;
            --texto-primario: #1E293B;
            --texto-secundario: #475569;
            --texto-dica: #94A3B8;
            --sucesso: #059669;
            --sucesso-fundo: #DCFCE7;
            --sucesso-texto: #166534;
            --alerta: #D97706;
            --alerta-fundo: #FEF3C7;
            --alerta-texto: #92400E;
            --erro: #DC2626;
            --erro-fundo: #FEE2E2;
            --erro-texto: #991B1B;
            --info: #0284C7;
            --info-fundo: #E0F2FE;
            --info-texto: #075985;
            --ciano: #0891B2;
            --ciano-fundo: #CFFAFE;
            --ciano-texto: #155E75;
        }
        body {
            background: linear-gradient(180deg, #E2E8F0 0%, var(--fundo) 320px);
            color: var(--texto-primario);
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            min-height: 100vh;
        }
        .navbar-fiea {
            background: linear-gradient(100deg, #003264 0%, var(--fiea-azul-escuro) 28%, var(--fiea-azul) 75%, #11599F 100%);
            padding: 12px 0;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.08);
        }
        .navbar-brand {
            font-weight: 700;
            color: #FFFFFF !important;
            font-size: 1.25rem;
            letter-spacing: -0.2px;
        }
        .badge-pill-status {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(52, 211, 153, 0.16);
            color: #A7F3D0;
            border: 1px solid rgba(52, 211, 153, 0.45);
            font-size: 0.78rem;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 999px;
            letter-spacing: 0.03em;
            white-space: nowrap;
        }
        .dot-pulse {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #34D399;
            animation: dotPulse 1.7s ease-out infinite;
        }
        @keyframes dotPulse {
            0% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55); }
            70% { box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }
            100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
        }
        @media (prefers-reduced-motion: reduce) {
            .dot-pulse { animation: none; }
        }
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 999px;
            font-size: 0.86rem;
            font-weight: 600;
            border: 1px solid transparent;
            transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
        }
        .chip-ok { background: var(--sucesso-fundo); color: var(--sucesso-texto); border-color: #86EFAC; }
        .chip-warn { background: var(--alerta-fundo); color: var(--alerta-texto); border-color: #FCD34D; }
        .chip-off { background: #E9EDF3; color: #33415C; border-color: #C9D3E0; }
        .hero-card {
            background: linear-gradient(165deg, #E9F1FB 0%, #F5FAFF 52%, #FFFFFF 100%);
            border: 1px solid #C9DCF0;
        }
        .token-box {
            background: linear-gradient(180deg, #FFFFFF 0%, #EFF6FF 100%);
            box-shadow: 0 8px 22px rgba(0, 78, 166, 0.16);
        }
        .secao-label {
            font-size: 0.72rem;
            letter-spacing: 0.09em;
        }
        .metric-card {
            transition: transform 0.18s ease, box-shadow 0.18s ease;
        }
        .metric-card:hover {
            transform: translateY(-4px);
            box-shadow: 0 14px 32px rgba(16, 42, 67, 0.16);
        }
        .metric-icon {
            width: 48px;
            height: 48px;
            border-radius: 13px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
        }
        .metric-icon.verde { background: #D1FAE5; color: #047857; }
        .metric-icon.azul { background: #DBEAFE; color: #1E40AF; }
        .metric-icon.ciano { background: #CFFAFE; color: #155E75; }
        .metric-number {
            font-size: 2.4rem;
            line-height: 1.1;
        }
        .panel-icon {
            width: 36px;
            height: 36px;
            border-radius: 10px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 auto;
        }
        .panel-icon.verde { background: #D1FAE5; color: #047857; }
        .panel-icon.azul { background: #DBEAFE; color: #1E40AF; }
        .config-box {
            background: #F2F7FE;
            border: 1px solid #D5E4F5;
        }
        .rodape {
            border-top: 1px solid var(--contorno);
            background: #FFFFFF;
        }
        .nav-pill-btn {
            border-radius: 20px;
            padding: 6px 16px;
            font-size: 0.85rem;
            text-decoration: none;
            transition: all 0.15s ease;
            display: inline-flex;
            align-items: center;
        }
        .nav-pill-btn.active {
            background-color: #FFFFFF;
            color: var(--fiea-azul) !important;
            font-weight: 600;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .nav-pill-btn:not(.active) {
            background-color: rgba(255, 255, 255, 0.12);
            color: #FFFFFF !important;
        }
        .nav-pill-btn:not(.active):hover {
            background-color: rgba(255, 255, 255, 0.22);
        }
        .card {
            border-radius: 12px;
            border: 1px solid var(--contorno);
            background-color: var(--superficie);
            box-shadow: 0 3px 18px rgba(16, 42, 67, 0.10);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            vertical-align: middle;
        }
        .status-dot.online {
            background-color: var(--sucesso);
            box-shadow: 0 0 0 2px rgba(5, 150, 105, 0.2);
        }
        .status-dot.offline {
            background-color: var(--texto-dica);
        }
        .status-dot.warning {
            background-color: var(--alerta);
            box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.2);
        }
        .btn-primary {
            background-color: var(--fiea-azul);
            border-color: var(--fiea-azul);
            font-weight: 500;
        }
        .btn-primary:hover, .btn-primary:focus {
            background-color: var(--fiea-azul-escuro);
            border-color: var(--fiea-azul-escuro);
        }
    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-fiea mb-4">
        <div class="container-fluid px-4 pagina-container">
            <a class="navbar-brand d-flex align-items-center" href="/">
                <img src="/static/logo_fiea.jpg" alt="FIEA" width="45" height="45" class="me-2 rounded">
                Inventário
            </a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="nav-pill-btn active">Início</a>
                <a href="/bens" class="nav-pill-btn">Bens Cadastrados</a>
                <a href="/salas" class="nav-pill-btn">Gerenciar Salas</a>
                <a href="/relatorio" class="nav-pill-btn">Relatório de Inventário</a>
                <span class="badge-pill-status ms-2"><span class="dot-pulse"></span>Servidor Ativo</span>
            </div>
        </div>
    </nav>

    <div class="container px-4 py-2" style="max-width: 1140px;">
        <!-- Seção 1: Conexão com o Leitor Urovo DT50 (Hero Card) -->
        <div class="card mb-4 hero-card shadow-sm">
            <div class="card-body p-4 text-center">
                <div class="d-flex justify-content-center align-items-center gap-2 mb-2">
                    <span id="badgeColetor" class="chip chip-off">
                        <span id="dotColetor" class="status-dot offline"></span>
                        <span id="textoColetor">Coletor: Aguardando Conexão Wi-Fi</span>
                    </span>
                </div>
                <h5 class="fw-bold mb-1" style="color: var(--fiea-azul); font-size: 1.4rem;">Conexão com o Leitor Urovo DT50</h5>
                <p class="text-muted small mb-3">
                    Certifique-se de que o leitor e este computador estejam na <strong>mesma rede Wi-Fi</strong>. Digite o <strong>Token</strong> no aplicativo:
                </p>

                <!-- Apenas Token de conexão mascarável com botão de visualização -->
                <div class="d-inline-flex flex-column align-items-center py-3 px-5 rounded-3 border border-primary border-opacity-50 mb-3 token-box">
                    <div class="small fw-semibold text-muted mb-1 text-uppercase" style="letter-spacing: 0.5px; font-size: 0.75rem;">Token de Conexão</div>
                    <div class="d-flex align-items-center gap-2">
                        <span class="fs-3 font-monospace fw-bold text-primary px-3 py-1 bg-light rounded border campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 2px;">••••••••••</span>
                        <button type="button" class="btn btn-outline-secondary py-1 px-2 btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                            <svg class="icone-olho-aberto" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                            <svg class="icone-olho-fechado" style="display:none;" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                        </button>
                    </div>
                </div>

                <div class="row justify-content-center">
                    <div class="col-md-9 text-start">
                        <div class="p-3 rounded border small config-box">
                            <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-2">
                                <strong class="text-dark">Como configurar no leitor Urovo DT50:</strong>
                                <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 fw-medium" style="font-size: 0.78rem;" data-bs-toggle="modal" data-bs-target="#modalComoConectarHome">
                                    Ajuda e Endereço Completo
                                </button>
                            </div>
                            <ol class="mb-0 ps-3 text-secondary">
                                <li>No aplicativo do Urovo, toque na barra de conexão no topo da tela.</li>
                                <li>Digite os 8 caracteres do Token acima (sem hífen).</li>
                                <li>Toque em <strong>Salvar e testar</strong> para sincronizar com o computador.</li>
                            </ol>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Modal Didático: Ajuda e Conexão Manual com o Leitor Urovo DT50 (Home) -->
        <div class="modal fade modal-conexao-ajuda" id="modalComoConectarHome" tabindex="-1" aria-labelledby="modalComoConectarHomeLabel" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content border-0 shadow">
                    <div class="modal-header bg-light">
                        <h6 class="modal-title fw-bold text-primary" id="modalComoConectarHomeLabel">
                            Ajuda de Conexão com o Leitor Urovo DT50
                        </h6>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fechar"></button>
                    </div>
                    <div class="modal-body p-4">
                        <ol class="list-group list-group-numbered list-group-flush mb-3">
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Conecte o Leitor na Mesma Rede Wi-Fi</div>
                                    <span class="text-muted small">O leitor Urovo DT50 e este computador precisam estar na mesma rede.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Abra a Configuração no Leitor</div>
                                    <span class="text-muted small">No aplicativo Urovo, toque na barra de conexão no topo ou no menu lateral em <strong>Configurações</strong>.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 1: Token Rápido (Recomendada)</div>
                                    <span class="text-muted small">Digite diretamente os 8 caracteres (sem hífen):</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-dark fs-5 campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 2: Conexão Direta (Endereço Completo)</div>
                                    <span class="text-muted small">Se preferir digitar o endereço completo, clique no olho para revelar:</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-primary fs-6 campo-endereco-mascaravel" data-real="http://{{ ip_local }}:5000" data-mascarado="••••••••••••••••" style="letter-spacing: 1px;">••••••••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-endereco" title="Mostrar ou ocultar endereço completo" aria-label="Mostrar ou ocultar endereço completo">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Toque em "Salvar e testar"</div>
                                    <span class="text-muted small">Ao confirmar a mensagem de sucesso, o status ficará verde e os dados estarão sincronizados.</span>
                                </div>
                            </li>
                        </ol>
                    </div>
                    <div class="modal-footer bg-light py-2">
                        <button type="button" class="btn btn-primary btn-sm px-4" data-bs-dismiss="modal">Entendi</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- Seção 2: Métricas Rápidas do Sistema (3 Cards) -->
        <div class="row g-3 mb-4">
            <div class="col-md-4">
                <div class="card h-100 p-4 text-center border-0 shadow-sm bg-white metric-card">
                    <div class="metric-icon verde mx-auto mb-2">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"></path><rect x="9" y="3" width="6" height="4" rx="1"></rect><path d="M9 14l2 2 4-4"></path></svg>
                    </div>
                    <div class="text-muted small text-uppercase fw-semibold secao-label mb-2">Conferência &amp; Auditoria</div>
                    <div class="metric-number fw-bold text-success mb-1">{{ total_sessoes }}</div>
                    <div class="small text-muted mb-3">Sessões de inventário registradas</div>
                    <a href="/relatorio" class="btn btn-sm btn-outline-success mt-auto fw-semibold">
                        Acessar Relatório de Inventário &rarr;
                    </a>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card h-100 p-4 text-center border-0 shadow-sm bg-white metric-card">
                    <div class="metric-icon azul mx-auto mb-2">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
                    </div>
                    <div class="text-muted small text-uppercase fw-semibold secao-label mb-2">Base Patrimonial</div>
                    <div class="metric-number fw-bold text-primary mb-1">{{ total_bens }}</div>
                    <div class="small text-muted mb-3">Bens cadastrados no sistema</div>
                    <a href="/bens" class="btn btn-sm btn-outline-primary mt-auto fw-semibold">
                        Consultar Bens Cadastrados &rarr;
                    </a>
                </div>
            </div>
            <div class="col-md-4">
                <div class="card h-100 p-4 text-center border-0 shadow-sm bg-white metric-card">
                    <div class="metric-icon ciano mx-auto mb-2">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path><polyline points="9 22 9 12 15 12 15 22"></polyline></svg>
                    </div>
                    <div class="text-muted small text-uppercase fw-semibold secao-label mb-2">Estrutura Física</div>
                    <div class="metric-number fw-bold text-primary mb-1">{{ total_salas }}</div>
                    <div class="small text-muted mb-3">Salas e ambientes mapeados</div>
                    <a href="/salas" class="btn btn-sm btn-outline-primary mt-auto fw-semibold">
                        Gerenciar Salas &rarr;
                    </a>
                </div>
            </div>
        </div>

        <!-- Seção 3: Orientação Didática: Rotina vs Manutenção -->
        <div class="row g-4 mb-4">
            <!-- Rotina Operacional -->
            <div class="col-md-7">
                <div class="card h-100 border-0 shadow-sm" style="border-left: 4px solid var(--sucesso) !important;">
                    <div class="card-body p-4">
                        <div class="d-flex align-items-center gap-3 mb-3">
                            <span class="panel-icon verde">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"></path><rect x="9" y="3" width="6" height="4" rx="1"></rect><path d="M9 14l2 2 4-4"></path></svg>
                            </span>
                            <div>
                                <span class="badge bg-success-subtle text-success fw-bold text-uppercase mb-1" style="font-size: 0.68rem; letter-spacing: 0.08em;">Dia a Dia</span>
                                <h6 class="fw-bold mb-0 text-dark" style="font-size: 1.02rem;">Rotina Padrão do Inventário RFID</h6>
                            </div>
                        </div>
                        <p class="text-muted small mb-3">
                            Para fazer o inventário de rotina, <strong>não é necessário recadastrar itens nem salas</strong>. O processo é direto no leitor Urovo:
                        </p>
                        <ol class="small text-secondary ps-3 mb-3" style="line-height: 1.7;">
                            <li><strong>Início no Urovo:</strong> Abra o aplicativo no leitor portátil e selecione a sala que vai auditar.</li>
                            <li><strong>Varredura com Gatilho:</strong> Acione o gatilho RFID e percorra o ambiente até bipar todas as etiquetas.</li>
                            <li><strong>Finalização e Sincronismo:</strong> Encerre a varredura e toque em <em>Sincronizar</em> (ou os dados entram automaticamente pelo Wi-Fi).</li>
                            <li><strong>Auditoria no Computador:</strong> Abra a aba <a href="/relatorio" class="fw-semibold text-success text-decoration-none">Relatório de Inventário</a> para conferir itens localizados, fora de local ou pendências.</li>
                        </ol>
                        <a href="/relatorio" class="btn btn-success btn-sm px-3 fw-semibold">Abrir Relatório de Inventário</a>
                    </div>
                </div>
            </div>

            <!-- Manutenção Cadastral -->
            <div class="col-md-5">
                <div class="card h-100 border-0 shadow-sm" style="border-left: 4px solid var(--fiea-azul) !important;">
                    <div class="card-body p-4">
                        <div class="d-flex align-items-center gap-3 mb-3">
                            <span class="panel-icon azul">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"></path></svg>
                            </span>
                            <div>
                                <span class="badge bg-primary-subtle text-primary fw-bold text-uppercase mb-1" style="font-size: 0.68rem; letter-spacing: 0.08em;">Eventual</span>
                                <h6 class="fw-bold mb-0 text-dark" style="font-size: 1.02rem;">Manutenção de Cadastro</h6>
                            </div>
                        </div>
                        <p class="text-muted small mb-3">
                            Utilize estas opções apenas quando houver alterações patrimoniais ou novos ambientes:
                        </p>
                        <ul class="small text-secondary ps-3 mb-4" style="line-height: 1.7;">
                            <li><a href="/salas" class="text-primary fw-semibold text-decoration-none">Gerenciar Salas:</a> Crie ou edite ambientes físicos (ex: novas salas, laboratórios ou almoxarifado).</li>
                            <li><a href="/bens" class="text-primary fw-semibold text-decoration-none">Bens Cadastrados:</a> Adicione novos equipamentos que chegaram ou vincule novas tags RFID (EPC).</li>
                        </ul>
                        <div class="alert mb-0 py-2 small" style="background:#F2F7FE; border:1px solid #D5E4F5; color:#33415C;">
                            <strong>Dica:</strong> Uma vez cadastradas, as salas e os bens ficam salvos permanentemente no banco de dados.
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <footer class="rodape">
        <div class="container px-4 py-3 d-flex justify-content-between align-items-center flex-wrap gap-2 small text-muted" style="max-width: 1140px;">
            <span><strong class="text-secondary">FIEA Inventário RFID</strong> · Servidor local nesta máquina</span>
            <span>Dados armazenados em <code>dados/inventario.db</code></span>
        </div>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Monitoramento e alternância de máscara (Token e Endereço Completo)
        let tokenVisivel = false;
        let enderecoVisivel = false;

        function alternarMascaraToken() {
            tokenVisivel = !tokenVisivel;
            document.querySelectorAll('.campo-token-mascaravel').forEach(el => {
                el.innerText = tokenVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-token').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = tokenVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = tokenVisivel ? 'inline-block' : 'none';
                }
            });
        }

        function alternarMascaraEndereco() {
            enderecoVisivel = !enderecoVisivel;
            document.querySelectorAll('.campo-endereco-mascaravel').forEach(el => {
                el.innerText = enderecoVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = enderecoVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = enderecoVisivel ? 'inline-block' : 'none';
                }
            });
        }

        document.querySelectorAll('.btn-toggle-token').forEach(btn => {
            btn.addEventListener('click', alternarMascaraToken);
        });
        document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
            btn.addEventListener('click', alternarMascaraEndereco);
        });

        // Ao fechar qualquer modal de conexão/ajuda, reoculta o endereço completo por privacidade
        document.querySelectorAll('.modal-conexao-ajuda').forEach(modalEl => {
            modalEl.addEventListener('hidden.bs.modal', () => {
                if (enderecoVisivel) {
                    alternarMascaraEndereco();
                }
            });
        });

        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        if (data.fila_pendente && data.fila_pendente > 0) {
                            badge.className = 'chip chip-warn';
                            dot.className = 'status-dot warning';
                            texto.innerText = `Coletor Conectado · ${data.fila_pendente} pendência(s) na fila`;
                        } else {
                            badge.className = 'chip chip-ok';
                            dot.className = 'status-dot online';
                            const tempo = (data.segundos_atras !== null && data.segundos_atras !== undefined) ? `há ${data.segundos_atras}s` : 'ativo';
                            texto.innerText = `Coletor Conectado`;
                        }
                    } else {
                        badge.className = 'chip chip-off';
                        dot.className = 'status-dot offline';
                        texto.innerText = 'Coletor: Aguardando Conexão Wi-Fi';
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

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>FIEA Inventário - Gestão de Bens &amp; Servidor Local</title>
    <link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACy0lEQVR42u1X7UtTYRS/JVgL+lIfoj4U1Mc+9Af0oaToBXpHShalIBlUWF8KelMpi0KpoKiMQiOxZbJJmJVvm+415wtJbbaZuc1pbZPNuTXny073nMu9LPdSH5q3YAcO9z7P+e2c3/O757l3D8PwlpG5hFm78wyzsaid2VMDKXHMjTWw1i8mWb6aySozpazwXMdaWFNY+XwWjyZBSqAk812cd6yd0mf+Jz0hWnHe0wTSBP5JAvuvd4CsYwgM/W7QmV3Q2OWEI7d0wEgVwJR+4PxwPWFzyjTwUmMjrJ71N91O2HVFJeTKva2DBuMw6M1uqFZ9hWXSuuQEdpQoIRKJwFw78aATmNPtwFSNAlPeD0yOHA7eVMdgcZx/10C5Nl9oEeL89Vrtx+QEFHo7Ab2BMBTcew/HWMfrqjwFR+B4E2y93AoLWGy9wUFYpydIGB6/Jp9Tp07L5TI7fGB3Bej+/mtLcgKB0BQBUa7F2S/IFx2QUSz7hho+2XygMbkgk53z/+Cw+AgkLE4ShV1xVA4ToWmKl9T0QffAGN1XvLUmJoAJoiXj/fPwOK3Y4Q5S7HHTAKw/2RAXa7R4KNepCqMwtzJXDkarh7APkxHYdL457vPHgusKXgnjYnZFWRdb4mJROcxlHfHT2MZKv/uqCixO/+8VOFfVK6xoQ2GjIGvGvufU7fyKthW1wdnKnoRYHMdTB+1p62BiAsgO7bs3RJJHxwofdQkJlx6qpW2KNjsbgbw7epCWa8lxmz1hFUMLTk5DW9838vEg1y9ynT0xgXc9IwQyWsdi3g2VLV8oFgrP0LiU3U7RW4/3LZdawTsRpnl8fyAWF8P3QHPvaGICWra7TXYflMlNMQRwy2HsmZKTEFVAxVCtyakZ2radbANuL24jHDr2Cf97mXqI5qqVg3//Vbxwb/pjlCbwXxMQ/W+56AcT0Y9moh9ORT6e/wQsXsMsZqQz6gAAAABJRU5ErkJggg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon.png?v=fiea">
    <link rel="icon" type="image/x-icon" href="/favicon.ico?v=fiea">
    <link rel="shortcut icon" href="/favicon.ico?v=fiea">
    <link rel="apple-touch-icon" href="/favicon.png?v=fiea">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root {
            --fiea-azul: #004EA6;
            --fiea-azul-escuro: #003A7C;
            --fiea-azul-claro: #00A0E7;
            --fiea-azul-fundo: #E6EEF8;
            --fundo: #F1F5F9;
            --superficie: #FFFFFF;
            --contorno: #E2E8F0;
            --contorno-forte: #CBD5E1;
            --texto-primario: #1E293B;
            --texto-secundario: #475569;
            --texto-dica: #64748B;
            --sucesso: #059669;
            --sucesso-fundo: #DCFCE7;
            --sucesso-texto: #166534;
            --alerta: #D97706;
            --alerta-fundo: #FEF3C7;
            --alerta-texto: #92400E;
            --erro: #DC2626;
            --erro-fundo: #FEE2E2;
            --erro-texto: #991B1B;
            --info: #0284C7;
            --info-fundo: #E0F2FE;
            --info-texto: #075985;
            --ciano: #0891B2;
            --ciano-fundo: #CFFAFE;
            --ciano-texto: #155E75;
        }
        body {
            background: linear-gradient(180deg, #E2E8F0 0%, var(--fundo) 320px);
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: var(--texto-primario);
            -webkit-font-smoothing: antialiased;
        }
        .navbar-fiea {
            background: linear-gradient(100deg, #003264 0%, var(--fiea-azul-escuro) 28%, var(--fiea-azul) 75%, #11599F 100%);
            border-bottom: 1px solid var(--fiea-azul-escuro);
        }
        .navbar-brand {
            font-weight: 700;
            letter-spacing: -0.01em;
            color: #FFFFFF !important;
            font-size: 1.25rem;
        }
        .nav-pill-btn {
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 0.875rem;
            text-decoration: none;
            transition: all 0.15s ease-in-out;
            display: inline-flex;
            align-items: center;
        }
        .nav-pill-btn.active {
            background-color: #FFFFFF;
            color: var(--fiea-azul) !important;
            font-weight: 600;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .nav-pill-btn:not(.active) {
            background-color: rgba(255, 255, 255, 0.12);
            color: #FFFFFF !important;
        }
        .nav-pill-btn:not(.active):hover {
            background-color: rgba(255, 255, 255, 0.22);
        }
        .card {
            border-radius: 12px;
            border: 1px solid var(--contorno);
            background-color: var(--superficie);
            box-shadow: 0 3px 18px rgba(16, 42, 67, 0.10);
        }
        .card-header {
            background-color: #F3F8FE;
            border-bottom: 1px solid #DFE9F6;
        }
        .card-header-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: var(--fiea-azul-fundo);
            color: var(--fiea-azul);
            flex: 0 0 auto;
        }
        .tabela-cadastro thead th {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #3D5068;
            white-space: nowrap;
            background: #E8EEF7;
            border-bottom: 2px solid #CFDCEC;
        }
        .tabela-cadastro tbody td {
            padding-top: 0.85rem;
            padding-bottom: 0.85rem;
            border-bottom-color: #ECF1F8;
        }
        .tabela-cadastro tbody tr:nth-child(even) {
            background-color: #FAFCFF;
        }
        .tabela-cadastro tbody tr:hover {
            background-color: #E8F0FB;
        }
        .pagina-container {
            max-width: 1240px;
            margin-left: auto;
            margin-right: auto;
        }
        /* Formulário de cadastro — polimento visual */
        .form-card { max-width: 1040px; }
        .form-card .form-label { color: #33415C; letter-spacing: 0.02em; }
        .form-card .form-control-lg,
        .form-card .form-select-lg {
            border-color: #D3DEEA;
            border-radius: 10px;
            color: #1E293B;
        }
        .form-card .form-control-lg:focus,
        .form-card .form-select-lg:focus {
            border-color: var(--fiea-azul);
            box-shadow: 0 0 0 3px rgba(0, 78, 166, 0.15);
        }
        .form-card .form-control-lg::placeholder { color: #A8B7CB; }
        .form-card .form-text { color: #64748B; }
        .form-card .input-group-lg .form-control { border-radius: 10px 0 0 10px; border-color: #D3DEEA; }
        .form-card .input-group-lg .btn-outline-secondary {
            border-color: #D3DEEA;
            border-left: none;
            border-radius: 0 10px 10px 0;
            background: #F1F5F9;
            color: #3D4B5F;
            font-weight: 600;
        }
        .form-card .input-group-lg .btn-outline-secondary:hover,
        .form-card .input-group-lg .btn-outline-secondary:focus {
            background: #E2E8F0;
            color: #1E293B;
            border-color: #CBD5E1;
        }
        .btn-fiea {
            background: linear-gradient(180deg, #0F63B4 0%, var(--fiea-azul) 55%, #003A7C 140%);
            border: 1px solid var(--fiea-azul-escuro);
            box-shadow: 0 6px 16px rgba(0, 78, 166, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.22);
            color: #FFFFFF;
            font-weight: 600;
            letter-spacing: 0.01em;
        }
        .btn-fiea:hover,
        .btn-fiea:focus {
            background: linear-gradient(180deg, #0B5CB8 0%, var(--fiea-azul-escuro) 120%);
            border-color: var(--fiea-azul-escuro);
            color: #FFFFFF;
        }
        .limpar-link { color: #64748B; font-weight: 500; }
        .limpar-link:hover { color: var(--fiea-azul); }
        .metal-card {
            background: linear-gradient(180deg, #F0F7FF 0%, #E9F0FB 100%);
            border: 1px solid #C4D8EE;
            border-radius: 12px;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }
        .metal-card:hover { border-color: #A9C8E8; box-shadow: 0 2px 10px rgba(0, 78, 166, 0.12); }
        .divider-suave { border-top: 1px solid #E4ECF7; }
        .tag-mono {
            display: inline-flex;
            align-items: center;
            background: #EDF2F9;
            color: #16324F;
            border: 1px solid #C9D8EA;
            border-radius: 6px;
            padding: 3px 8px;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.76rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }
        .chip-contador {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: var(--fiea-azul-fundo);
            color: var(--fiea-azul-escuro);
            border: 1px solid #BFD5EE;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 4px 12px;
            white-space: nowrap;
        }
        .chip-sala {
            display: inline-flex;
            align-items: center;
            background: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .chip-alerta {
            display: inline-flex;
            align-items: center;
            background: var(--alerta-fundo);
            color: var(--alerta-texto);
            border: 1px solid #FCD34D;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .chip-neutro {
            display: inline-flex;
            align-items: center;
            background: #ECEFF4;
            color: #3D4B5F;
            border: 1px solid #D3DAE4;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .badge-pill-status {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(52, 211, 153, 0.16);
            color: #A7F3D0;
            border: 1px solid rgba(52, 211, 153, 0.45);
            font-size: 0.78rem;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 999px;
            letter-spacing: 0.03em;
            white-space: nowrap;
        }
        .dot-pulse {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #34D399;
            animation: dotPulse 1.7s ease-out infinite;
        }
        @keyframes dotPulse {
            0% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55); }
            70% { box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }
            100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
        }
        @media (prefers-reduced-motion: reduce) {
            .dot-pulse { animation: none; }
        }
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 999px;
            font-size: 0.86rem;
            font-weight: 600;
            border: 1px solid transparent;
            transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
            width: max-content;
        }
        .chip-ok { background: var(--sucesso-fundo); color: var(--sucesso-texto); border-color: #86EFAC; }
        .chip-warn { background: var(--alerta-fundo); color: var(--alerta-texto); border-color: #FCD34D; }
        .chip-off { background: #E9EDF3; color: #33415C; border-color: #C9D3E0; }
        .rodape {
            border-top: 1px solid var(--contorno);
            background: #FFFFFF;
        }
        @media print {
            .no-print { display: none !important; }
        }
        .ip-box {
            background: linear-gradient(135deg, #E9F0FB 0%, #DDE9F7 52%, #CCDFEF 100%);
            border: 1px solid #AECBE6;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 3px 14px rgba(0, 78, 166, 0.14);
        }
        .btn-primary {
            background-color: var(--fiea-azul);
            border-color: var(--fiea-azul);
            font-weight: 500;
        }
        .btn-primary:hover, .btn-primary:focus {
            background-color: var(--fiea-azul-escuro);
            border-color: var(--fiea-azul-escuro);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            vertical-align: middle;
        }
        .status-dot.online {
            background-color: var(--sucesso);
            box-shadow: 0 0 0 2px rgba(5, 150, 105, 0.2);
        }
        .status-dot.offline {
            background-color: var(--texto-dica);
        }
        .status-dot.warning {
            background-color: var(--alerta);
            box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.2);
        }
        .badge-localizado {
            background-color: var(--sucesso-fundo);
            color: var(--sucesso-texto);
            border: 1px solid #86EFAC;
            font-weight: 600;
        }
        .badge-fora-local {
            background-color: var(--alerta-fundo);
            color: var(--alerta-texto);
            border: 1px solid #FDE68A;
            font-weight: 600;
        }
        .badge-nao-localizado {
            background-color: var(--erro-fundo);
            color: var(--erro-texto);
            border: 1px solid #FECACA;
            font-weight: 600;
        }
        .badge-sem-cobertura {
            background-color: var(--fundo);
            color: var(--texto-secundario);
            border: 1px solid var(--contorno-forte);
            font-weight: 600;
        }
        .badge-sobras {
            background-color: var(--ciano-fundo);
            color: var(--ciano-texto);
            border: 1px solid #A5F3FC;
            font-weight: 600;
        }
        .badge-pendente {
            background-color: #FEF3C7;
            color: #92400E;
            border: 1px solid #FCD34D;
            font-weight: 600;
        }
        .badge-metal {
            background-color: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            font-weight: 500;
        }

        /* Estilos da Etiqueta Patrimonial e Impressao */
        .etiqueta-patrimonial-box {
            background: #ffffff;
            border: 2px solid #1E293B;
            border-radius: 8px;
            padding: 14px 16px;
            color: #0f172a;
            max-width: 450px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            font-family: system-ui, -apple-system, sans-serif;
        }
        .etiqueta-header {
            border-bottom: 2px solid #004EA6;
            padding-bottom: 6px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .etiqueta-brand {
            font-size: 1.05rem;
            font-weight: 800;
            color: #004EA6;
            letter-spacing: 0.04em;
        }
        .etiqueta-qrcode-wrapper {
            background: #ffffff;
            border: 1px solid #cbd5e1;
            border-radius: 6px;
            padding: 6px;
            display: inline-flex;
            justify-content: center;
            align-items: center;
            min-width: 122px;
            min-height: 122px;
        }
        .etiqueta-qrcode-wrapper img, .etiqueta-qrcode-wrapper canvas {
            display: block;
            margin: 0 auto;
        }

        @media print {
            body {
                background: #ffffff !important;
                color: #000000 !important;
            }
            body > *:not(#areaImpressaoEtiquetaContainer) {
                display: none !important;
            }
            .modal-backdrop, .modal {
                display: none !important;
            }
            #areaImpressaoEtiquetaContainer {
                display: block !important;
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                margin: 0;
                padding: 4mm;
            }
            .etiqueta-patrimonial-box {
                border: 2px solid #000000 !important;
                box-shadow: none !important;
                page-break-inside: avoid;
                width: 90mm;
                max-width: 90mm;
                padding: 4mm;
                margin: 0 auto;
            }
            .etiqueta-header {
                border-bottom: 2px solid #000000 !important;
            }
            .etiqueta-brand {
                color: #000000 !important;
            }
        }
        @media screen {
            #areaImpressaoEtiquetaContainer {
                display: none !important;
            }
        }

    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-fiea mb-4">
        <div class="container-fluid px-4 pagina-container">
            <a class="navbar-brand d-flex align-items-center" href="/">
                <img src="/static/logo_fiea.jpg" alt="FIEA" width="45" height="45" class="me-2 rounded">
                Inventário
            </a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="nav-pill-btn">Início</a>
                <a href="/bens" class="nav-pill-btn active">Bens Cadastrados</a>
                <a href="/salas" class="nav-pill-btn">Gerenciar Salas</a>
                <a href="/relatorio" class="nav-pill-btn">Relatório de Inventário</a>
                <span class="badge-pill-status ms-2"><span class="dot-pulse"></span>Servidor Ativo</span>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4 pagina-container">
        <!-- Topo da Tela de Bens: Identificação e Conexão Limpa -->
        <div class="ip-box mb-4 d-flex flex-wrap justify-content-between align-items-center gap-3">
            <div>
                <div class="d-flex align-items-center gap-2 mb-1">
                    <h6 class="text-primary mb-0 fw-bold">Cadastro e Associação de Bens Patrimoniais</h6>
                    <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 fw-medium" style="font-size: 0.76rem;" data-bs-toggle="modal" data-bs-target="#modalComoConectarBens">
                        Como Conectar o Leitor?
                    </button>
                </div>
                <p class="mb-0 text-muted small d-flex flex-wrap align-items-center gap-2">
                    <span>Token de Conexão:</span>
                    <strong class="text-dark small font-monospace px-2 py-1 bg-white rounded border campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</strong>
                    <button type="button" class="btn btn-link btn-sm p-0 text-secondary btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                        <svg class="icone-olho-aberto" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                        <svg class="icone-olho-fechado" style="display:none;" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                    </button>
                </p>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div id="badgeColetor" class="chip chip-off">
                    <span id="dotColetor" class="status-dot offline"></span>
                    <span id="textoColetor">Coletor: Aguardando Wi-Fi</span>
                </div>
                <div class="text-end">
                    <span class="chip-contador">Total de Bens: {{ bens|length }}</span>
                    <span class="chip-contador">Salas: {{ salas|length }}</span>
                </div>
            </div>
        </div>

        <!-- Modal Didático: Como Conectar o Leitor Urovo DT50 (Bens) -->
        <div class="modal fade modal-conexao-ajuda" id="modalComoConectarBens" tabindex="-1" aria-labelledby="modalComoConectarBensLabel" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content border-0 shadow">
                    <div class="modal-header bg-light">
                        <h6 class="modal-title fw-bold text-primary" id="modalComoConectarBensLabel">
                            Como Conectar o Leitor Urovo DT50 ao Servidor
                        </h6>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fechar"></button>
                    </div>
                    <div class="modal-body p-4">
                        <ol class="list-group list-group-numbered list-group-flush mb-3">
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Conecte o Leitor na Mesma Rede Wi-Fi</div>
                                    <span class="text-muted small">O leitor Urovo DT50 e este computador precisam estar na mesma rede.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Abra a Configuração no Leitor</div>
                                    <span class="text-muted small">No menu lateral do aplicativo Urovo, toque em <strong>Configurações</strong> ou na barra de conexão no topo.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 1: Token Rápido (Recomendada)</div>
                                    <span class="text-muted small">Digite diretamente os 8 caracteres (sem hífen):</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-dark fs-5 campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 2: Conexão Direta (Endereço Completo)</div>
                                    <span class="text-muted small">Se preferir digitar o endereço completo, clique no olho para revelar:</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-primary fs-6 campo-endereco-mascaravel" data-real="http://{{ ip_local }}:5000" data-mascarado="••••••••••••••••" style="letter-spacing: 1px;">••••••••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-endereco" title="Mostrar ou ocultar endereço completo" aria-label="Mostrar ou ocultar endereço completo">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Toque em "Salvar e testar"</div>
                                    <span class="text-muted small">Ao confirmar a mensagem de sucesso, os dados serão sincronizados e você poderá capturar tags diretamente no formulário.</span>
                                </div>
                            </li>
                        </ol>
                    </div>
                    <div class="modal-footer bg-light py-2">
                        <button type="button" class="btn btn-primary btn-sm px-4" data-bs-dismiss="modal">Entendi</button>
                    </div>
                </div>
            </div>
        </div>

        {% if mensagem %}
        <div class="alert alert-{{ tipo_msg or 'info' }} alert-dismissible fade show d-flex flex-wrap justify-content-between align-items-center gap-3 mb-4" role="alert">
            <div class="d-flex align-items-center gap-2">
                {% if tipo_msg == 'success' %}
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" class="text-success flex-shrink-0">
                    <polyline points="20 6 9 17 4 12"></polyline>
                </svg>
                {% endif %}
                <div>{{ mensagem }}</div>
            </div>
            {% if bem_etiqueta %}
            <div>
                <button type="button" class="btn btn-success btn-sm fw-bold d-inline-flex align-items-center gap-2 shadow-sm text-nowrap btn-abrir-etiqueta"
                        data-patrimonio="{{ bem_etiqueta.patrimonio }}"
                        data-descricao="{{ bem_etiqueta.descricao or '' }}"
                        data-sala="{{ bem_etiqueta.sala or 'Sem sala' }}"
                        data-epc="{{ bem_etiqueta.epc or '' }}"
                        data-metalico="{{ 1 if bem_etiqueta.metalico else 0 }}">
                    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="6 9 6 2 18 2 18 9"></polyline>
                        <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
                        <rect x="6" y="14" width="12" height="8"></rect>
                    </svg>
                    Imprimir Etiqueta / QR Code
                </button>
            </div>
            {% else %}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Fechar"></button>
            {% endif %}
        </div>
        {% endif %}

        {% if conflito %}
        <div class="alert alert-warning border-warning shadow-sm mb-4" role="alert">
            <div class="d-flex align-items-start gap-3">
                <div class="pt-1">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-warning">
                        <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
                        <line x1="12" y1="9" x2="12" y2="13"></line>
                        <line x1="12" y1="17" x2="12.01" y2="17"></line>
                    </svg>
                </div>
                <div class="flex-grow-1">
                    <h6 class="fw-bold mb-1">Tag RFID já associada a outro bem</h6>
                    <p class="mb-1 small">
                        A tag <code>{{ conflito.epc }}</code> já está cadastrada no bem <strong>{{ conflito.dono_pat }}</strong> ({{ conflito.dono_desc }}) na sala <strong>{{ conflito.dono_sala }}</strong>.
                    </p>
                    <p class="mb-3 small text-muted">
                        Deseja transferir esta tag para o bem <strong>{{ conflito.novo_pat }}</strong> ({{ conflito.novo_desc }})? O bem anterior ({{ conflito.dono_pat }}) ficará sem tag RFID.
                    </p>
                    <div class="d-flex gap-2">
                        <form method="POST" action="/cadastrar">
                            <input type="hidden" name="patrimonio" value="{{ conflito.novo_pat }}">
                            <input type="hidden" name="descricao" value="{{ conflito.novo_desc }}">
                            <input type="hidden" name="sala" value="{{ conflito.novo_sala }}">
                            <input type="hidden" name="epc" value="{{ conflito.epc }}">
                            <input type="hidden" name="metalico" value="{{ conflito.novo_metal }}">
                            <input type="hidden" name="editando" value="{{ conflito.editando }}">
                            <input type="hidden" name="confirmar_transferencia" value="1">
                            <button type="submit" class="btn btn-warning btn-sm fw-bold">Transferir Tag para este Bem</button>
                        </form>
                        <a href="/bens" class="btn btn-outline-secondary btn-sm">Cancelar</a>
                    </div>
                </div>
            </div>
        </div>
        {% endif %}

        <div class="row g-4">
            <!-- Formulário de Cadastro de Novo Bem -->
            <div class="col-12">
                <div class="card">
                    <div class="card-header py-3 d-flex justify-content-between align-items-center gap-3 flex-wrap">
                        <div class="d-flex align-items-center gap-3">
                            <span class="card-header-icon">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <circle cx="12" cy="12" r="9"></circle>
                                    <path d="M12 8v8M8 12h8"></path>
                                </svg>
                            </span>
                            <div>
                                <h6 class="mb-0 fw-bold">Cadastrar Novo Bem</h6>
                                <div class="small text-muted">Preencha os dados abaixo ou capture a tag diretamente do leitor Urovo.</div>
                            </div>
                        </div>
                        <span class="chip-neutro">Cadastro manual e via leitor</span>
                    </div>
                    <div class="card-body">
                        <form method="POST" action="/cadastrar" id="formNovoBem" class="row g-3 align-items-start form-card">
                            <input type="hidden" name="editando" value="0">
                            <div class="col-12 col-md-3">
                                <label class="form-label small fw-semibold">Patrimônio / Tombo *</label>
                                <input type="text" name="patrimonio" id="campoPatrimonio" class="form-control form-control-lg" placeholder="Ex: 10001" required>
                                <div id="statusPatrimonio" class="form-text small text-muted">O patrimônio é único por bem.</div>
                            </div>
                            <div class="col-12 col-md-9">
                                <label class="form-label small fw-semibold">Descrição do Bem *</label>
                                <input type="text" name="descricao" id="campoDescricao" class="form-control form-control-lg" placeholder="Ex: Notebook Dell Latitude" required>
                            </div>
                            <div class="col-12 col-md-6 col-lg-5">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <label class="form-label small fw-semibold mb-0">EPC da Tag RFID</label>
                                    <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2" id="btnCapturarTag" style="font-size: 0.78rem;">
                                        Capturar do Urovo
                                    </button>
                                </div>
                                <div class="input-group input-group-lg">
                                    <input type="text" name="epc" id="campoEpc" class="form-control form-control-lg font-monospace" placeholder="Ex: E2801160600002162A7A1C57">
                                    <button type="button" class="btn btn-outline-secondary" id="btnLimparCampoEpc" title="Limpar tag">
                                        Limpar
                                    </button>
                                </div>
                                <div id="statusCaptura" class="form-text small text-muted">Deixe em branco se ainda não foi etiquetado.</div>
                            </div>
                            <div class="col-12 col-md-6 col-lg-4">
                                <div class="d-flex justify-content-between align-items-center mb-1">
                                    <label class="form-label small fw-semibold mb-0">Sala Esperada *</label>
                                    <a href="/salas" class="text-decoration-none small text-primary fw-medium" style="font-size: 0.76rem;">+ Nova Sala</a>
                                </div>
                                <select name="sala" id="campoSala" class="form-select form-select-lg" required>
                                    <option value="" disabled selected>Selecione uma sala...</option>
                                    {% for s in salas %}
                                    <option value="{{ s.id }}">{{ s.nome }} ({{ s.unidade }})</option>
                                    {% endfor %}
                                </select>
                            </div>
                            <div class="col-12 col-md-6 col-lg-3">
                                <label class="form-label small fw-semibold mb-1" for="checkMetal">Material do bem</label>
                                <div class="metal-card px-3 d-flex align-items-center" style="min-height: 48px;">
                                    <div class="form-check mb-0 py-1">
                                        <input class="form-check-input" type="checkbox" name="metalico" id="checkMetal" value="1" style="width: 1.15rem; height: 1.15rem; accent-color: var(--fiea-azul);">
                                        <label class="form-check-label small fw-medium" for="checkMetal">
                                            Bem metálico
                                            <span class="text-muted d-block" style="font-size: 0.72rem;">Requer tag on-metal</span>
                                        </label>
                                    </div>
                                </div>
                            </div>
                            <div class="col-12">
                                <div class="d-flex align-items-center gap-3 flex-wrap divider-suave pt-3">
                                    <button type="submit" id="btnSubmitForm" class="btn btn-fiea btn-lg px-5">Salvar Bem no Cadastro</button>
                                    <span class="d-none d-sm-block" style="width: 1px; height: 22px; background: #D8E2EE;"></span>
                                    <button type="button" id="btnLimparFormNovoBem" class="btn btn-link btn-sm limpar-link text-decoration-none">Limpar campos</button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Tabela de Bens Cadastrados -->
            <div class="col-12">
                <div class="card">
                    <div class="card-header py-3 d-flex justify-content-between align-items-center gap-3 flex-wrap">
                        <div class="d-flex align-items-center gap-3">
                            <span class="card-header-icon">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <line x1="8" y1="6" x2="21" y2="6"></line>
                                    <line x1="8" y1="12" x2="21" y2="12"></line>
                                    <line x1="8" y1="18" x2="21" y2="18"></line>
                                    <line x1="3" y1="6" x2="3.01" y2="6"></line>
                                    <line x1="3" y1="12" x2="3.01" y2="12"></line>
                                    <line x1="3" y1="18" x2="3.01" y2="18"></line>
                                </svg>
                            </span>
                            <div>
                                <h6 class="mb-0 fw-bold">Relação Patrimonial de Bens</h6>
                                <div class="small text-muted">Todos os bens cadastrados, com suas tags RFID e salas esperadas.</div>
                            </div>
                        </div>
                        <div class="d-flex align-items-center gap-2 flex-wrap">
                            <span class="chip-contador">{{ bens|length }} {% if bens|length == 1 %}bem{% else %}bens{% endif %}</span>
                            <div class="input-group input-group-sm" style="width: 280px;">
                                <span class="input-group-text bg-white border-end-0">
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted">
                                        <circle cx="11" cy="11" r="8"></circle>
                                        <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                                    </svg>
                                </span>
                                <input type="text" id="filtro" class="form-control border-start-0 ps-0" placeholder="Filtrar por patrimônio, descrição ou sala...">
                            </div>
                        </div>
                    </div>
                    <div class="card-body p-0">
                        <div class="table-responsive" style="max-height: 520px; overflow-y: auto;">
                            <table class="table table-hover align-middle mb-0 tabela-cadastro" id="tabelaBens">
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
                                        <td class="fw-bold font-monospace">{{ b.patrimonio }}</td>
                                        <td>{{ b.descricao }}</td>
                                        <td>{% if b.sala %}<span class="chip-sala">{{ b.sala }}</span>{% else %}<span class="chip-alerta">Sem sala</span>{% endif %}</td>
                                        <td class="font-monospace small">
                                            {% if b.epc %}
                                                <span class="tag-mono">{{ b.epc }}</span>
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
                                        <td class="text-center text-nowrap">
                                            <button type="button" class="btn btn-sm btn-outline-dark py-0 px-2 me-1 btn-abrir-etiqueta"
                                                    data-patrimonio="{{ b.patrimonio }}"
                                                    data-descricao="{{ b.descricao or '' }}"
                                                    data-sala="{{ b.sala or 'Sem sala' }}"
                                                    data-epc="{{ b.epc or '' }}"
                                                    data-metalico="{{ 1 if b.metalico else 0 }}"
                                                    title="Imprimir Etiqueta / QR Code">
                                                <svg style="pointer-events: none;" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                    <polyline points="6 9 6 2 18 2 18 9"></polyline>
                                                    <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
                                                    <rect x="6" y="14" width="12" height="8"></rect>
                                                </svg>
                                            </button>
                                            <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 me-1 btn-editar-bem"
                                                    data-bs-toggle="modal" data-bs-target="#modalEditarBem"
                                                    data-patrimonio="{{ b.patrimonio }}"
                                                    data-descricao="{{ b.descricao or '' }}"
                                                    data-sala="{{ b.sala_esperada_id or '' }}"
                                                    data-epc="{{ b.epc or '' }}"
                                                    data-metalico="{{ 1 if b.metalico else 0 }}"
                                                    title="Editar bem">
                                                <svg style="pointer-events: none;" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                    <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path>
                                                    <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path>
                                                </svg>
                                            </button>
                                            <form method="POST" action="/excluir/{{ b.patrimonio }}" onsubmit="return confirm('Excluir o bem {{ b.patrimonio }}?');" style="display:inline;">
                                                <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-2" title="Excluir">
                                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                        <polyline points="3 6 5 6 21 6"></polyline>
                                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                                    </svg>
                                                </button>
                                            </form>
                                        </td>
                                    </tr>
                                    {% else %}
                                    <tr>
                                        <td colspan="6" class="text-center py-5">
                                            <div class="text-muted mb-2">
                                                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="opacity-50">
                                                    <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
                                                    <polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>
                                                    <line x1="12" y1="22.08" x2="12" y2="12"></line>
                                                </svg>
                                            </div>
                                            <h6 class="fw-bold mb-1">Nenhum bem cadastrado ainda</h6>
                                            <p class="text-muted small mb-0">Utilize o formulário acima para cadastrar o primeiro bem do patrimônio.</p>
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

    <!-- Modal: Editar Bem -->
    <div class="modal fade" id="modalEditarBem" tabindex="-1" aria-labelledby="modalEditarBemLabel" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content border-0 shadow">
                <div class="modal-header bg-light">
                    <h5 class="modal-title fw-bold" id="modalEditarBemLabel">Editar Bem</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fechar"></button>
                </div>
                <form method="POST" action="/cadastrar" id="formModalEditarBem">
                    <input type="hidden" name="editando" value="1">
                    <div class="modal-body p-4">
                        <div class="mb-3">
                            <label class="form-label small fw-semibold">Patrimônio / Tombo</label>
                            <input type="text" name="patrimonio" id="modalEditPatrimonio" class="form-control font-monospace bg-light" readonly required>
                            <div class="form-text small text-muted">O patrimônio é o identificador único do bem e não pode ser alterado.</div>
                        </div>
                        <div class="mb-3">
                            <label class="form-label small fw-semibold">Descrição do Bem *</label>
                            <input type="text" name="descricao" id="modalEditDescricao" class="form-control" placeholder="Ex: Notebook Dell Latitude" required>
                        </div>
                        <div class="mb-3">
                            <div class="d-flex justify-content-between align-items-center mb-1">
                                <label class="form-label small fw-semibold mb-0">Sala Esperada *</label>
                                <a href="/salas" class="text-decoration-none small text-primary fw-medium" style="font-size: 0.76rem;">+ Nova Sala</a>
                            </div>
                            <select name="sala" id="modalEditSala" class="form-select" required>
                                <option value="" disabled>Selecione uma sala...</option>
                                {% for s in salas %}
                                <option value="{{ s.id }}">{{ s.nome }} ({{ s.unidade }})</option>
                                {% endfor %}
                            </select>
                        </div>
                        <div class="mb-3">
                            <div class="d-flex justify-content-between align-items-center mb-1">
                                <label class="form-label small fw-semibold mb-0">EPC da Tag RFID</label>
                                <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2" id="btnModalCapturarTag" style="font-size: 0.78rem;">
                                    Capturar do Urovo
                                </button>
                            </div>
                            <div class="input-group">
                                <input type="text" name="epc" id="modalEditEpc" class="form-control font-monospace" placeholder="Ex: E2801160600002162A7A1C57">
                                <button type="button" class="btn btn-outline-secondary btn-sm" id="btnModalLimparEpc" title="Limpar tag">
                                    Limpar
                                </button>
                            </div>
                            <div id="modalEditStatusCaptura" class="form-text small text-muted">Deixe em branco para desvincular a tag RFID.</div>
                        </div>
                        <div class="form-check">
                            <input class="form-check-input" type="checkbox" name="metalico" id="modalEditMetal" value="1">
                            <label class="form-check-label small" for="modalEditMetal">
                                Bem metálico (requer tag on-metal)
                            </label>
                        </div>
                    </div>
                    <div class="modal-footer bg-light py-2 d-flex justify-content-between">
                        <button type="button" class="btn btn-outline-dark btn-sm d-inline-flex align-items-center gap-1" id="btnModalImprimirEtiqueta" title="Visualizar e imprimir etiqueta patrimonial com QR Code">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="6 9 6 2 18 2 18 9"></polyline>
                                <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
                                <rect x="6" y="14" width="12" height="8"></rect>
                            </svg>
                            Imprimir Etiqueta
                        </button>
                        <div class="d-flex gap-2">
                            <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Cancelar</button>
                            <button type="submit" id="btnModalSalvar" class="btn btn-success btn-sm fw-semibold px-3">Salvar Alterações</button>
                        </div>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <!-- Modal: Pré-visualização e Impressão de Etiqueta com QR Code -->
    <div class="modal fade" id="modalEtiqueta" tabindex="-1" aria-labelledby="modalEtiquetaLabel" aria-hidden="true">
        <div class="modal-dialog modal-dialog-centered">
            <div class="modal-content border-0 shadow">
                <div class="modal-header bg-light">
                    <h5 class="modal-title fw-bold" id="modalEtiquetaLabel">
                        Etiqueta Patrimonial · <span class="font-monospace text-primary" id="etiquetaModalPatrimonio"></span>
                    </h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fechar"></button>
                </div>
                <div class="modal-body p-4 bg-light bg-opacity-50">
                    <div class="text-center mb-3">
                        <small class="text-muted fw-semibold text-uppercase" style="font-size: 0.72rem; letter-spacing: 0.05em;">
                            Pré-visualização da Etiqueta
                        </small>
                    </div>

                    <!-- Cartão da Etiqueta -->
                    <div id="etiquetaCard" class="etiqueta-patrimonial-box mx-auto">
                        <div class="etiqueta-header">
                            <div class="d-flex align-items-center gap-2">
                                <span class="etiqueta-brand">SISTEMA FIEA</span>
                                <span class="badge bg-secondary text-white py-1 px-2" style="font-size: 0.65rem;">PATRIMÔNIO</span>
                            </div>
                            <div id="etiquetaTagMetalBadge" class="badge bg-warning text-dark border border-dark fw-bold d-none" style="font-size: 0.68rem;">
                                TAG ON-METAL
                            </div>
                        </div>

                        <div class="d-flex align-items-center gap-3">
                            <div class="text-center">
                                <div id="etiquetaQrcodeContainer" class="etiqueta-qrcode-wrapper"></div>
                                <div class="text-muted font-monospace mt-1" style="font-size: 0.62rem; letter-spacing: 0.05em;">SCAN QR / RFID</div>
                            </div>

                            <div class="flex-grow-1 overflow-hidden">
                                <div class="mb-1">
                                    <span class="text-muted text-uppercase fw-semibold" style="font-size: 0.65rem;">Patrimônio</span>
                                    <div id="etiquetaTextoPatrimonio" class="fs-4 fw-bold font-monospace text-dark lh-1"></div>
                                </div>
                                <div class="mb-1">
                                    <span class="text-muted text-uppercase fw-semibold" style="font-size: 0.65rem;">Descrição</span>
                                    <div id="etiquetaTextoDescricao" class="fw-semibold text-dark text-truncate" style="font-size: 0.85rem;"></div>
                                </div>
                                <div class="mb-1">
                                    <span class="text-muted text-uppercase fw-semibold" style="font-size: 0.65rem;">Localização</span>
                                    <div id="etiquetaTextoSala" class="text-secondary small text-truncate"></div>
                                </div>
                                <div>
                                    <span class="text-muted text-uppercase fw-semibold" style="font-size: 0.62rem;">EPC Tag</span>
                                    <div id="etiquetaTextoEpc" class="font-monospace text-muted text-break" style="font-size: 0.68rem; line-height: 1.2;"></div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- Explicação Didática -->
                    <div class="alert alert-info border-info mt-4 mb-0 py-2 px-3 small d-flex align-items-start gap-2" role="alert">
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-info mt-1 flex-shrink-0">
                            <circle cx="12" cy="12" r="10"></circle>
                            <line x1="12" y1="16" x2="12" y2="12"></line>
                            <line x1="12" y1="8" x2="12.01" y2="8"></line>
                        </svg>
                        <div id="etiquetaDicaQr" class="small">
                            O QR Code codifica o <strong>código EPC</strong> da tag RFID. Aponte a câmera do leitor Urovo DT50 para leitura instantânea no inventário.
                        </div>
                    </div>
                </div>
                <div class="modal-footer bg-light py-2 d-flex justify-content-between">
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="baixarImagemQrCode()" title="Baixar imagem do QR Code em PNG">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="me-1">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        Baixar QR Code (PNG)
                    </button>
                    <div class="d-flex gap-2">
                        <button type="button" class="btn btn-outline-secondary btn-sm" data-bs-dismiss="modal">Fechar</button>
                        <button type="button" class="btn btn-primary btn-sm fw-semibold px-3 d-inline-flex align-items-center gap-2" onclick="dispararImpressaoEtiqueta()">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <polyline points="6 9 6 2 18 2 18 9"></polyline>
                                <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
                                <rect x="6" y="14" width="12" height="8"></rect>
                            </svg>
                            Imprimir Etiqueta
                        </button>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Container isolado para impressao da etiqueta patrimonial -->
    <div id="areaImpressaoEtiquetaContainer"></div>

    <footer class="rodape no-print">
        <div class="container-fluid px-4 pagina-container py-3 d-flex justify-content-between align-items-center flex-wrap gap-2 small text-muted">
            <span><strong class="text-secondary">FIEA Inventário RFID</strong> · Servidor local nesta máquina</span>
            <span>Dados armazenados em <code>dados/inventario.db</code></span>
        </div>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="/js/qrcode.min.js"></script>
    <script>
        if (typeof QRCode === 'undefined') {
            document.write('<script src="https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"><' + '/script>');
        }
    </script>
    <script>
        document.getElementById('filtro').addEventListener('keyup', function() {
            let valor = this.value.toLowerCase();
            let linhas = document.querySelectorAll('#tabelaBens tbody tr');
            linhas.forEach(linha => {
                let texto = linha.innerText.toLowerCase();
                linha.style.display = texto.includes(valor) ? '' : 'none';
            });
        });

        // Elementos do formulário de cadastro de novo bem
        const campoPatrimonio = document.getElementById('campoPatrimonio');
        const statusPatrimonio = document.getElementById('statusPatrimonio');
        const campoDescricao = document.getElementById('campoDescricao');
        const campoSala = document.getElementById('campoSala');
        const campoEpc = document.getElementById('campoEpc');
        const statusCaptura = document.getElementById('statusCaptura');
        const btnCapturar = document.getElementById('btnCapturarTag');
        const btnLimparCampoEpc = document.getElementById('btnLimparCampoEpc');
        const btnSubmitForm = document.getElementById('btnSubmitForm');
        const checkMetal = document.getElementById('checkMetal');

        // Elementos do modal de edição
        const modalEditarBemElement = document.getElementById('modalEditarBem');
        const modalEditPatrimonio = document.getElementById('modalEditPatrimonio');
        const modalEditDescricao = document.getElementById('modalEditDescricao');
        const modalEditSala = document.getElementById('modalEditSala');
        const modalEditEpc = document.getElementById('modalEditEpc');
        const modalEditMetal = document.getElementById('modalEditMetal');
        const modalEditStatusCaptura = document.getElementById('modalEditStatusCaptura');
        const btnModalLimparEpc = document.getElementById('btnModalLimparEpc');
        const btnModalCapturarTag = document.getElementById('btnModalCapturarTag');

        // Verificação de patrimônio duplicado em tempo real no cadastro
        let debouncePatrimonioTimer = null;
        function verificarPatrimonioDebounced() {
            clearTimeout(debouncePatrimonioTimer);
            debouncePatrimonioTimer = setTimeout(verificarPatrimonio, 250);
        }

        function verificarPatrimonio() {
            const pat = campoPatrimonio.value.trim();
            if (!pat) {
                campoPatrimonio.classList.remove('is-valid', 'is-invalid');
                statusPatrimonio.innerHTML = 'O patrimônio é único por bem.';
                statusPatrimonio.className = 'form-text small text-muted';
                btnSubmitForm.disabled = false;
                return;
            }

            fetch('/api/verificar-patrimonio?patrimonio=' + encodeURIComponent(pat))
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'disponivel') {
                        campoPatrimonio.classList.remove('is-invalid');
                        campoPatrimonio.classList.add('is-valid');
                        statusPatrimonio.innerHTML = 'Patrimônio disponível para cadastro.';
                        statusPatrimonio.className = 'form-text small text-success fw-semibold';
                        btnSubmitForm.disabled = false;
                    } else if (data.status === 'em_uso') {
                        campoPatrimonio.classList.remove('is-valid');
                        campoPatrimonio.classList.add('is-invalid');
                        statusPatrimonio.innerHTML = 'Atenção: Patrimônio já cadastrado (' + (data.descricao || 'Sem descrição') + ' na sala ' + (data.sala || 'Sem sala') + '). Cada bem deve ser único. Para editar este bem, utilize o botão Editar na tabela.';
                        statusPatrimonio.className = 'form-text small text-danger fw-bold';
                        btnSubmitForm.disabled = true;
                    }
                })
                .catch(() => {});
        }

        campoPatrimonio.addEventListener('input', verificarPatrimonioDebounced);

        // Verificação de EPC em tempo real no cadastro
        let debounceTimer = null;
        function verificarEpcDebounced() {
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(verificarEpc, 250);
        }

        function verificarEpc() {
            const epc = campoEpc.value.trim().toUpperCase();
            const pat = campoPatrimonio.value.trim();
            if (!epc) {
                campoEpc.classList.remove('is-valid', 'is-invalid');
                statusCaptura.innerHTML = 'Deixe em branco se ainda não foi etiquetado.';
                statusCaptura.className = 'form-text small text-muted';
                return;
            }

            fetch('/api/verificar-epc?epc=' + encodeURIComponent(epc) + '&patrimonio=' + encodeURIComponent(pat))
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'disponivel') {
                        campoEpc.classList.remove('is-invalid');
                        campoEpc.classList.add('is-valid');
                        statusCaptura.innerHTML = 'Tag disponível para cadastro.';
                        statusCaptura.className = 'form-text small text-success fw-bold';
                    } else if (data.status === 'proprio_bem') {
                        campoEpc.classList.remove('is-invalid', 'is-valid');
                        statusCaptura.innerHTML = 'Tag atualmente vinculada a este bem.';
                        statusCaptura.className = 'form-text small text-muted';
                    } else if (data.status === 'em_uso') {
                        campoEpc.classList.remove('is-valid');
                        campoEpc.classList.add('is-invalid');
                        statusCaptura.innerHTML = 'Atenção: Tag já vinculada ao bem <strong>' + data.patrimonio + '</strong> (' + data.descricao + ') em <strong>' + data.sala + '</strong>. Ao salvar, você poderá transferi-la.';
                        statusCaptura.className = 'form-text small text-danger fw-bold';
                    }
                })
                .catch(() => {});
        }

        campoEpc.addEventListener('input', verificarEpcDebounced);

        if (btnLimparCampoEpc) {
            btnLimparCampoEpc.addEventListener('click', function() {
                campoEpc.value = '';
                campoEpc.classList.remove('is-valid', 'is-invalid');
                statusCaptura.innerHTML = 'Deixe em branco se ainda não foi etiquetado.';
                statusCaptura.className = 'form-text small text-muted';
                salvarRascunhoBem();
            });
        }

        // Rascunho automático em sessionStorage para manter campos preenchidos ao trocar de página
        const CHAVE_RASCUNHO_BEM = 'rascunho_novo_bem';

        function salvarRascunhoBem() {
            const rascunho = {
                patrimonio: campoPatrimonio ? campoPatrimonio.value : '',
                descricao: campoDescricao ? campoDescricao.value : '',
                epc: campoEpc ? campoEpc.value : '',
                sala: campoSala ? campoSala.value : '',
                metalico: checkMetal ? checkMetal.checked : false
            };
            if (rascunho.patrimonio || rascunho.descricao || rascunho.epc || rascunho.sala || rascunho.metalico) {
                sessionStorage.setItem(CHAVE_RASCUNHO_BEM, JSON.stringify(rascunho));
            } else {
                sessionStorage.removeItem(CHAVE_RASCUNHO_BEM);
            }
        }

        function restaurarRascunhoBem() {
            {% if tipo_msg == 'success' %}
            sessionStorage.removeItem(CHAVE_RASCUNHO_BEM);
            return;
            {% endif %}

            const salvo = sessionStorage.getItem(CHAVE_RASCUNHO_BEM);
            if (!salvo) return;
            try {
                const dados = JSON.parse(salvo);
                if (campoPatrimonio && !campoPatrimonio.value && dados.patrimonio) {
                    campoPatrimonio.value = dados.patrimonio;
                    verificarPatrimonio();
                }
                if (campoDescricao && !campoDescricao.value && dados.descricao) {
                    campoDescricao.value = dados.descricao;
                }
                if (campoEpc && !campoEpc.value && dados.epc) {
                    campoEpc.value = dados.epc;
                    verificarEpc();
                }
                if (campoSala && (!campoSala.value || campoSala.value === '') && dados.sala) {
                    campoSala.value = dados.sala;
                }
                if (checkMetal && dados.metalico !== undefined) {
                    checkMetal.checked = !!dados.metalico;
                }
            } catch (e) {
                sessionStorage.removeItem(CHAVE_RASCUNHO_BEM);
            }
        }

        if (campoPatrimonio) campoPatrimonio.addEventListener('input', salvarRascunhoBem);
        if (campoDescricao) campoDescricao.addEventListener('input', salvarRascunhoBem);
        if (campoEpc) campoEpc.addEventListener('input', salvarRascunhoBem);
        if (campoSala) campoSala.addEventListener('change', salvarRascunhoBem);
        if (checkMetal) checkMetal.addEventListener('change', salvarRascunhoBem);

        const formNovoBem = document.getElementById('formNovoBem');
        if (formNovoBem) {
            formNovoBem.addEventListener('submit', function() {
                sessionStorage.removeItem(CHAVE_RASCUNHO_BEM);
            });
        }

        const btnLimparFormNovoBem = document.getElementById('btnLimparFormNovoBem');
        if (btnLimparFormNovoBem) {
            btnLimparFormNovoBem.addEventListener('click', function() {
                if (campoPatrimonio) {
                    campoPatrimonio.value = '';
                    campoPatrimonio.classList.remove('is-valid', 'is-invalid');
                    statusPatrimonio.innerHTML = 'O patrimônio é único por bem.';
                    statusPatrimonio.className = 'form-text small text-muted';
                }
                if (campoDescricao) campoDescricao.value = '';
                if (campoEpc) {
                    campoEpc.value = '';
                    campoEpc.classList.remove('is-valid', 'is-invalid');
                    statusCaptura.innerHTML = 'Deixe em branco se ainda não foi etiquetado.';
                    statusCaptura.className = 'form-text small text-muted';
                }
                if (campoSala) campoSala.selectedIndex = 0;
                if (checkMetal) checkMetal.checked = false;
                if (btnSubmitForm) btnSubmitForm.disabled = false;
                sessionStorage.removeItem(CHAVE_RASCUNHO_BEM);
            });
        }

        restaurarRascunhoBem();

        // Captura rápida de tag RFID via Urovo (Ponte Coletor -> PC) no cadastro
        let pollingCaptura = null;
        if (btnCapturar) {
            btnCapturar.addEventListener('click', function() {
                if (pollingCaptura) {
                    clearInterval(pollingCaptura);
                    pollingCaptura = null;
                    btnCapturar.innerText = 'Capturar do Urovo';
                    btnCapturar.className = 'btn btn-sm btn-outline-primary py-0 px-2';
                    statusCaptura.innerHTML = 'Deixe em branco se ainda não foi etiquetado.';
                    statusCaptura.className = 'form-text small text-muted';
                    return;
                }

                btnCapturar.innerText = 'Aguardando leitura... (Cancelar)';
                btnCapturar.className = 'btn btn-sm btn-warning py-0 px-2';
                statusCaptura.innerHTML = '<span class="spinner-border spinner-border-sm text-primary me-1"></span> Aproxime o Urovo da etiqueta e clique em <strong>"Transmitir Tag ao PC"</strong> no coletor...';
                statusCaptura.className = 'form-text small text-primary fw-semibold';

                pollingCaptura = setInterval(() => {
                    fetch('/api/tag-capturada')
                        .then(r => r.json())
                        .then(data => {
                            if (data && data.epc) {
                                campoEpc.value = data.epc;
                                verificarEpc();
                                salvarRascunhoBem();
                                clearInterval(pollingCaptura);
                                pollingCaptura = null;
                                btnCapturar.innerText = 'Capturar do Urovo';
                                btnCapturar.className = 'btn btn-sm btn-outline-success py-0 px-2';
                            }
                        })
                        .catch(() => {});
                }, 800);
            });
        }

        // Abertura e preenchimento do Modal de Edição de Bem
        document.querySelectorAll('.btn-editar-bem').forEach(btn => {
            btn.addEventListener('click', function(e) {
                const targetBtn = this.closest('.btn-editar-bem') || this;
                const pat = targetBtn.dataset.patrimonio;
                const desc = targetBtn.dataset.descricao || '';
                const sala = targetBtn.dataset.sala || '';
                const epc = targetBtn.dataset.epc || '';
                const metal = (targetBtn.dataset.metalico === '1' || targetBtn.dataset.metalico === 1);

                if (modalEditPatrimonio) modalEditPatrimonio.value = pat;
                if (modalEditDescricao) modalEditDescricao.value = desc;
                if (modalEditSala) modalEditSala.value = sala;
                if (modalEditEpc) {
                    modalEditEpc.value = epc;
                    modalEditEpc.classList.remove('is-valid', 'is-invalid');
                }
                if (modalEditMetal) modalEditMetal.checked = metal;

                if (modalEditStatusCaptura) {
                    if (epc) {
                        modalEditStatusCaptura.innerHTML = 'Tag associada: <code>' + epc + '</code>. Use "Limpar" para desvincular.';
                        modalEditStatusCaptura.className = 'form-text small text-muted';
                    } else {
                        modalEditStatusCaptura.innerHTML = 'Este bem está sem tag RFID associada.';
                        modalEditStatusCaptura.className = 'form-text small text-muted';
                    }
                }

                if (window.bootstrap && modalEditarBemElement) {
                    const modalObj = bootstrap.Modal.getOrCreateInstance(modalEditarBemElement);
                    modalObj.show();
                }
            });
        });

        // Verificação de EPC no Modal de Edição
        let debounceModalEpcTimer = null;
        function verificarModalEpcDebounced() {
            clearTimeout(debounceModalEpcTimer);
            debounceModalEpcTimer = setTimeout(verificarModalEpc, 250);
        }

        function verificarModalEpc() {
            const epc = modalEditEpc.value.trim().toUpperCase();
            const pat = modalEditPatrimonio.value.trim();
            if (!epc) {
                modalEditEpc.classList.remove('is-valid', 'is-invalid');
                modalEditStatusCaptura.innerHTML = 'Tag removida. Ao salvar, o item ficará sem tag RFID.';
                modalEditStatusCaptura.className = 'form-text small text-warning fw-semibold';
                return;
            }

            fetch('/api/verificar-epc?epc=' + encodeURIComponent(epc) + '&patrimonio=' + encodeURIComponent(pat))
                .then(r => r.json())
                .then(data => {
                    if (data.status === 'disponivel') {
                        modalEditEpc.classList.remove('is-invalid');
                        modalEditEpc.classList.add('is-valid');
                        modalEditStatusCaptura.innerHTML = 'Tag disponível para este bem.';
                        modalEditStatusCaptura.className = 'form-text small text-success fw-bold';
                    } else if (data.status === 'proprio_bem') {
                        modalEditEpc.classList.remove('is-invalid', 'is-valid');
                        modalEditStatusCaptura.innerHTML = 'Tag atualmente vinculada a este bem.';
                        modalEditStatusCaptura.className = 'form-text small text-muted';
                    } else if (data.status === 'em_uso') {
                        modalEditEpc.classList.remove('is-valid');
                        modalEditEpc.classList.add('is-invalid');
                        modalEditStatusCaptura.innerHTML = 'Atenção: Tag já vinculada ao bem <strong>' + data.patrimonio + '</strong> (' + data.descricao + ') em <strong>' + data.sala + '</strong>. Ao salvar, você poderá transferi-la.';
                        modalEditStatusCaptura.className = 'form-text small text-danger fw-bold';
                    }
                })
                .catch(() => {});
        }

        modalEditEpc.addEventListener('input', verificarModalEpcDebounced);

        if (btnModalLimparEpc) {
            btnModalLimparEpc.addEventListener('click', function() {
                modalEditEpc.value = '';
                modalEditEpc.classList.remove('is-valid', 'is-invalid');
                modalEditStatusCaptura.innerHTML = 'Tag removida. Ao salvar, o item ficará sem tag RFID.';
                modalEditStatusCaptura.className = 'form-text small text-warning fw-semibold';
            });
        }

        // Captura rápida de tag RFID no Modal de Edição
        let modalPollingCaptura = null;
        if (btnModalCapturarTag) {
            btnModalCapturarTag.addEventListener('click', function() {
                if (modalPollingCaptura) {
                    clearInterval(modalPollingCaptura);
                    modalPollingCaptura = null;
                    btnModalCapturarTag.innerText = 'Capturar do Urovo';
                    btnModalCapturarTag.className = 'btn btn-sm btn-outline-primary py-0 px-2';
                    modalEditStatusCaptura.innerHTML = 'Deixe em branco para manter ou desvincular.';
                    modalEditStatusCaptura.className = 'form-text small text-muted';
                    return;
                }

                btnModalCapturarTag.innerText = 'Aguardando leitura... (Cancelar)';
                btnModalCapturarTag.className = 'btn btn-sm btn-warning py-0 px-2';
                modalEditStatusCaptura.innerHTML = '<span class="spinner-border spinner-border-sm text-primary me-1"></span> Aproxime o Urovo e clique em <strong>"Transmitir Tag ao PC"</strong>...';
                modalEditStatusCaptura.className = 'form-text small text-primary fw-semibold';

                modalPollingCaptura = setInterval(() => {
                    fetch('/api/tag-capturada')
                        .then(r => r.json())
                        .then(data => {
                            if (data && data.epc) {
                                modalEditEpc.value = data.epc;
                                verificarModalEpc();
                                clearInterval(modalPollingCaptura);
                                modalPollingCaptura = null;
                                btnModalCapturarTag.innerText = 'Capturar do Urovo';
                                btnModalCapturarTag.className = 'btn btn-sm btn-outline-success py-0 px-2';
                            }
                        })
                        .catch(() => {});
                }, 800);
            });
        }

        if (modalEditarBemElement) {
            modalEditarBemElement.addEventListener('hidden.bs.modal', function () {
                if (modalPollingCaptura) {
                    clearInterval(modalPollingCaptura);
                    modalPollingCaptura = null;
                    btnModalCapturarTag.innerText = 'Capturar do Urovo';
                    btnModalCapturarTag.className = 'btn btn-sm btn-outline-primary py-0 px-2';
                }
            });
        }

        // Gestao e Impressao de Etiqueta com QR Code
        let qrcodeInstance = null;

        function abrirModalEtiqueta(patrimonio, descricao, sala, epc, metalico) {
            const elPatrimonio = document.getElementById('etiquetaTextoPatrimonio');
            const elDescricao = document.getElementById('etiquetaTextoDescricao');
            const elSala = document.getElementById('etiquetaTextoSala');
            const elEpc = document.getElementById('etiquetaTextoEpc');
            const elModalPatrimonio = document.getElementById('etiquetaModalPatrimonio');
            const badgeMetal = document.getElementById('etiquetaTagMetalBadge');
            const containerQr = document.getElementById('etiquetaQrcodeContainer');
            const dicaQr = document.getElementById('etiquetaDicaQr');

            if (elModalPatrimonio) elModalPatrimonio.innerText = patrimonio;
            if (elPatrimonio) elPatrimonio.innerText = patrimonio;
            if (elDescricao) {
                elDescricao.innerText = descricao || 'Sem descrição';
                elDescricao.title = descricao || '';
            }
            if (elSala) elSala.innerText = sala || 'Sem sala';
            if (elEpc) elEpc.innerText = epc || 'Sem tag RFID vinculada';

            if (badgeMetal) {
                if (metalico === 1 || metalico === '1') {
                    badgeMetal.classList.remove('d-none');
                } else {
                    badgeMetal.classList.add('d-none');
                }
            }

            // O conteudo do QR Code e o EPC da tag RFID (ou identificador de contingencia PAT_<patrimonio>)
            const valorQr = (epc && epc.trim()) ? epc.trim() : ('PAT_' + String(patrimonio).trim());

            if (dicaQr) {
                if (epc && epc.trim()) {
                    dicaQr.innerHTML = 'O QR Code codifica o <strong>código EPC (' + valorQr + ')</strong> da tag RFID. Aponte a câmera do leitor Urovo DT50 para leitura instantânea no inventário.';
                } else {
                    dicaQr.innerHTML = 'Atenção: Este bem não possui tag RFID cadastrada. O QR Code foi gerado com o identificador de contingência <code>' + valorQr + '</code>.';
                }
            }

            if (containerQr) {
                containerQr.innerHTML = '';
                try {
                    if (typeof QRCode !== 'undefined') {
                        qrcodeInstance = new QRCode(containerQr, {
                            text: valorQr,
                            width: 110,
                            height: 110,
                            colorDark: "#000000",
                            colorLight: "#ffffff",
                            correctLevel: QRCode.CorrectLevel.M
                        });
                    } else {
                        containerQr.innerHTML = '<span class="text-danger small">QR Code indisponível</span>';
                    }
                } catch (e) {
                    containerQr.innerHTML = '<span class="text-danger small">Erro ao gerar QR</span>';
                }
            }

            const modalEl = document.getElementById('modalEtiqueta');
            if (modalEl && window.bootstrap) {
                const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
                modal.show();
            }
        }

        // Listener para abrir etiqueta atraves dos botoes da tabela e do alerta de sucesso
        document.querySelectorAll('.btn-abrir-etiqueta').forEach(btn => {
            btn.addEventListener('click', function(e) {
                const targetBtn = this.closest('.btn-abrir-etiqueta') || this;
                const pat = targetBtn.dataset.patrimonio;
                const desc = targetBtn.dataset.descricao || '';
                const sala = targetBtn.dataset.sala || '';
                const epc = targetBtn.dataset.epc || '';
                const metal = (targetBtn.dataset.metalico === '1' || targetBtn.dataset.metalico === 1) ? 1 : 0;
                abrirModalEtiqueta(pat, desc, sala, epc, metal);
            });
        });

        // Botao de imprimir etiqueta dentro do modalEditarBem
        const btnModalImprimirEtiqueta = document.getElementById('btnModalImprimirEtiqueta');
        if (btnModalImprimirEtiqueta) {
            btnModalImprimirEtiqueta.addEventListener('click', function() {
                const pat = modalEditPatrimonio.value;
                const desc = modalEditDescricao.value;
                const salaTexto = modalEditSala.options[modalEditSala.selectedIndex]?.text || '';
                const epc = modalEditEpc.value;
                const metal = modalEditMetal.checked ? 1 : 0;
                abrirModalEtiqueta(pat, desc, salaTexto, epc, metal);
            });
        }

        function dispararImpressaoEtiqueta() {
            const containerImpressao = document.getElementById('areaImpressaoEtiquetaContainer');
            const cardOriginal = document.getElementById('etiquetaCard');
            if (containerImpressao && cardOriginal) {
                containerImpressao.innerHTML = cardOriginal.outerHTML;
            }
            window.print();
        }

        function baixarImagemQrCode() {
            const containerQr = document.getElementById('etiquetaQrcodeContainer');
            if (!containerQr) return;
            const canvas = containerQr.querySelector('canvas');
            const img = containerQr.querySelector('img');
            const pat = document.getElementById('etiquetaTextoPatrimonio')?.innerText || 'etiqueta';

            let dataUrl = null;
            if (canvas) {
                dataUrl = canvas.toDataURL('image/png');
            } else if (img && img.src) {
                dataUrl = img.src;
            }

            if (dataUrl) {
                const link = document.createElement('a');
                link.download = `etiqueta_qrcode_${pat}.png`;
                link.href = dataUrl;
                link.click();
            }
        }

        // Monitoramento em tempo real do status de conexão com o coletor Urovo
        // Monitoramento e alternância de máscara (Token e Endereço Completo)
        let tokenVisivel = false;
        let enderecoVisivel = false;

        function alternarMascaraToken() {
            tokenVisivel = !tokenVisivel;
            document.querySelectorAll('.campo-token-mascaravel').forEach(el => {
                el.innerText = tokenVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-token').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = tokenVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = tokenVisivel ? 'inline-block' : 'none';
                }
            });
        }

        function alternarMascaraEndereco() {
            enderecoVisivel = !enderecoVisivel;
            document.querySelectorAll('.campo-endereco-mascaravel').forEach(el => {
                el.innerText = enderecoVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = enderecoVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = enderecoVisivel ? 'inline-block' : 'none';
                }
            });
        }

        document.querySelectorAll('.btn-toggle-token').forEach(btn => {
            btn.addEventListener('click', alternarMascaraToken);
        });
        document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
            btn.addEventListener('click', alternarMascaraEndereco);
        });

        // Ao fechar qualquer modal de conexão/ajuda, reoculta o endereço completo por privacidade
        document.querySelectorAll('.modal-conexao-ajuda').forEach(modalEl => {
            modalEl.addEventListener('hidden.bs.modal', () => {
                if (enderecoVisivel) {
                    alternarMascaraEndereco();
                }
            });
        });

        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        if (data.fila_pendente && data.fila_pendente > 0) {
                            badge.className = 'chip chip-warn';
                            dot.className = 'status-dot warning';
                            texto.innerText = `Coletor Conectado · ${data.fila_pendente} pendência(s) na fila`;
                        } else {
                            badge.className = 'chip chip-ok';
                            dot.className = 'status-dot online';
                            const tempo = (data.segundos_atras !== null && data.segundos_atras !== undefined) ? `há ${data.segundos_atras}s` : 'ativo';
                            texto.innerText = `Coletor Conectado`;
                        }
                    } else {
                        badge.className = 'chip chip-off';
                        dot.className = 'status-dot offline';
                        texto.innerText = 'Coletor: Aguardando Wi-Fi';
                    }
                })
                .catch(() => {});
        }
        setInterval(atualizarStatusColetor, 3000);
        atualizarStatusColetor();

        function copiarIpServidor() {
            const texto = 'http://{{ ip_local }}:5000';
            navigator.clipboard.writeText(texto).then(() => {
                const label = document.getElementById('labelCopiarIp');
                if (label) {
                    const original = label.innerText;
                    label.innerText = 'Copiado!';
                    setTimeout(() => { label.innerText = original; }, 2000);
                }
            }).catch(() => {
                prompt('Copie o endereço do servidor:', texto);
            });
        }
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
    <link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACy0lEQVR42u1X7UtTYRS/JVgL+lIfoj4U1Mc+9Af0oaToBXpHShalIBlUWF8KelMpi0KpoKiMQiOxZbJJmJVvm+415wtJbbaZuc1pbZPNuTXny073nMu9LPdSH5q3YAcO9z7P+e2c3/O757l3D8PwlpG5hFm78wyzsaid2VMDKXHMjTWw1i8mWb6aySozpazwXMdaWFNY+XwWjyZBSqAk812cd6yd0mf+Jz0hWnHe0wTSBP5JAvuvd4CsYwgM/W7QmV3Q2OWEI7d0wEgVwJR+4PxwPWFzyjTwUmMjrJ71N91O2HVFJeTKva2DBuMw6M1uqFZ9hWXSuuQEdpQoIRKJwFw78aATmNPtwFSNAlPeD0yOHA7eVMdgcZx/10C5Nl9oEeL89Vrtx+QEFHo7Ab2BMBTcew/HWMfrqjwFR+B4E2y93AoLWGy9wUFYpydIGB6/Jp9Tp07L5TI7fGB3Bej+/mtLcgKB0BQBUa7F2S/IFx2QUSz7hho+2XygMbkgk53z/+Cw+AgkLE4ShV1xVA4ToWmKl9T0QffAGN1XvLUmJoAJoiXj/fPwOK3Y4Q5S7HHTAKw/2RAXa7R4KNepCqMwtzJXDkarh7APkxHYdL457vPHgusKXgnjYnZFWRdb4mJROcxlHfHT2MZKv/uqCixO/+8VOFfVK6xoQ2GjIGvGvufU7fyKthW1wdnKnoRYHMdTB+1p62BiAsgO7bs3RJJHxwofdQkJlx6qpW2KNjsbgbw7epCWa8lxmz1hFUMLTk5DW9838vEg1y9ynT0xgXc9IwQyWsdi3g2VLV8oFgrP0LiU3U7RW4/3LZdawTsRpnl8fyAWF8P3QHPvaGICWra7TXYflMlNMQRwy2HsmZKTEFVAxVCtyakZ2radbANuL24jHDr2Cf97mXqI5qqVg3//Vbxwb/pjlCbwXxMQ/W+56AcT0Y9moh9ORT6e/wQsXsMsZqQz6gAAAABJRU5ErkJggg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon.png?v=fiea">
    <link rel="icon" type="image/x-icon" href="/favicon.ico?v=fiea">
    <link rel="shortcut icon" href="/favicon.ico?v=fiea">
    <link rel="apple-touch-icon" href="/favicon.png?v=fiea">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root {
            --fiea-azul: #004EA6;
            --fiea-azul-escuro: #003A7C;
            --fiea-azul-claro: #00A0E7;
            --fiea-azul-fundo: #E6EEF8;
            --fundo: #F1F5F9;
            --superficie: #FFFFFF;
            --contorno: #E2E8F0;
            --contorno-forte: #CBD5E1;
            --texto-primario: #1E293B;
            --texto-secundario: #475569;
            --texto-dica: #64748B;
            --sucesso: #059669;
            --sucesso-fundo: #DCFCE7;
            --sucesso-texto: #166534;
            --alerta: #D97706;
            --alerta-fundo: #FEF3C7;
            --alerta-texto: #92400E;
            --erro: #DC2626;
            --erro-fundo: #FEE2E2;
            --erro-texto: #991B1B;
            --info: #0284C7;
            --info-fundo: #E0F2FE;
            --info-texto: #075985;
            --ciano: #0891B2;
            --ciano-fundo: #CFFAFE;
            --ciano-texto: #155E75;
        }
        body {
            background: linear-gradient(180deg, #E2E8F0 0%, var(--fundo) 320px);
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: var(--texto-primario);
            -webkit-font-smoothing: antialiased;
        }
        .navbar-fiea {
            background: linear-gradient(100deg, #003264 0%, var(--fiea-azul-escuro) 28%, var(--fiea-azul) 75%, #11599F 100%);
            border-bottom: 1px solid var(--fiea-azul-escuro);
        }
        .navbar-brand {
            font-weight: 700;
            letter-spacing: -0.01em;
            color: #FFFFFF !important;
            font-size: 1.25rem;
        }
        .nav-pill-btn {
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 0.875rem;
            text-decoration: none;
            transition: all 0.15s ease-in-out;
            display: inline-flex;
            align-items: center;
        }
        .nav-pill-btn.active {
            background-color: #FFFFFF;
            color: var(--fiea-azul) !important;
            font-weight: 600;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .nav-pill-btn:not(.active) {
            background-color: rgba(255, 255, 255, 0.12);
            color: #FFFFFF !important;
        }
        .nav-pill-btn:not(.active):hover {
            background-color: rgba(255, 255, 255, 0.22);
        }
        .card {
            border-radius: 12px;
            border: 1px solid var(--contorno);
            background-color: var(--superficie);
            box-shadow: 0 3px 18px rgba(16, 42, 67, 0.10);
        }
        .card-header {
            background-color: #F3F8FE;
            border-bottom: 1px solid #DFE9F6;
        }
        .card-header-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: var(--fiea-azul-fundo);
            color: var(--fiea-azul);
            flex: 0 0 auto;
        }
        .tabela-cadastro thead th {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #3D5068;
            white-space: nowrap;
            background: #E8EEF7;
            border-bottom: 2px solid #CFDCEC;
        }
        .tabela-cadastro tbody td {
            padding-top: 0.85rem;
            padding-bottom: 0.85rem;
            border-bottom-color: #ECF1F8;
        }
        .tabela-cadastro tbody tr:nth-child(even) {
            background-color: #FAFCFF;
        }
        .tabela-cadastro tbody tr:hover {
            background-color: #E8F0FB;
        }
        .pagina-container {
            max-width: 1240px;
            margin-left: auto;
            margin-right: auto;
        }
        /* Formulário de cadastro — polimento visual */
        .form-card { max-width: 1040px; }
        .form-card .form-label { color: #33415C; letter-spacing: 0.02em; }
        .form-card .form-control-lg,
        .form-card .form-select-lg {
            border-color: #D3DEEA;
            border-radius: 10px;
            color: #1E293B;
        }
        .form-card .form-control-lg:focus,
        .form-card .form-select-lg:focus {
            border-color: var(--fiea-azul);
            box-shadow: 0 0 0 3px rgba(0, 78, 166, 0.15);
        }
        .form-card .form-control-lg::placeholder { color: #A8B7CB; }
        .form-card .form-text { color: #64748B; }
        .form-card .input-group-lg .form-control { border-radius: 10px 0 0 10px; border-color: #D3DEEA; }
        .form-card .input-group-lg .btn-outline-secondary {
            border-color: #D3DEEA;
            border-left: none;
            border-radius: 0 10px 10px 0;
            background: #F1F5F9;
            color: #3D4B5F;
            font-weight: 600;
        }
        .form-card .input-group-lg .btn-outline-secondary:hover,
        .form-card .input-group-lg .btn-outline-secondary:focus {
            background: #E2E8F0;
            color: #1E293B;
            border-color: #CBD5E1;
        }
        .btn-fiea {
            background: linear-gradient(180deg, #0F63B4 0%, var(--fiea-azul) 55%, #003A7C 140%);
            border: 1px solid var(--fiea-azul-escuro);
            box-shadow: 0 6px 16px rgba(0, 78, 166, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.22);
            color: #FFFFFF;
            font-weight: 600;
            letter-spacing: 0.01em;
        }
        .btn-fiea:hover,
        .btn-fiea:focus {
            background: linear-gradient(180deg, #0B5CB8 0%, var(--fiea-azul-escuro) 120%);
            border-color: var(--fiea-azul-escuro);
            color: #FFFFFF;
        }
        .limpar-link { color: #64748B; font-weight: 500; }
        .limpar-link:hover { color: var(--fiea-azul); }
        .metal-card {
            background: linear-gradient(180deg, #F0F7FF 0%, #E9F0FB 100%);
            border: 1px solid #C4D8EE;
            border-radius: 12px;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }
        .metal-card:hover { border-color: #A9C8E8; box-shadow: 0 2px 10px rgba(0, 78, 166, 0.12); }
        .divider-suave { border-top: 1px solid #E4ECF7; }
        .tag-mono {
            display: inline-flex;
            align-items: center;
            background: #EDF2F9;
            color: #16324F;
            border: 1px solid #C9D8EA;
            border-radius: 6px;
            padding: 3px 8px;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.76rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }
        .chip-contador {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: var(--fiea-azul-fundo);
            color: var(--fiea-azul-escuro);
            border: 1px solid #BFD5EE;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 4px 12px;
            white-space: nowrap;
        }
        .chip-sala {
            display: inline-flex;
            align-items: center;
            background: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .chip-alerta {
            display: inline-flex;
            align-items: center;
            background: var(--alerta-fundo);
            color: var(--alerta-texto);
            border: 1px solid #FCD34D;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .chip-neutro {
            display: inline-flex;
            align-items: center;
            background: #ECEFF4;
            color: #3D4B5F;
            border: 1px solid #D3DAE4;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .badge-pill-status {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(52, 211, 153, 0.16);
            color: #A7F3D0;
            border: 1px solid rgba(52, 211, 153, 0.45);
            font-size: 0.78rem;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 999px;
            letter-spacing: 0.03em;
            white-space: nowrap;
        }
        .dot-pulse {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #34D399;
            animation: dotPulse 1.7s ease-out infinite;
        }
        @keyframes dotPulse {
            0% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55); }
            70% { box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }
            100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
        }
        @media (prefers-reduced-motion: reduce) {
            .dot-pulse { animation: none; }
        }
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 999px;
            font-size: 0.86rem;
            font-weight: 600;
            border: 1px solid transparent;
            transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
            width: max-content;
        }
        .chip-ok { background: var(--sucesso-fundo); color: var(--sucesso-texto); border-color: #86EFAC; }
        .chip-warn { background: var(--alerta-fundo); color: var(--alerta-texto); border-color: #FCD34D; }
        .chip-off { background: #E9EDF3; color: #33415C; border-color: #C9D3E0; }
        .rodape {
            border-top: 1px solid var(--contorno);
            background: #FFFFFF;
        }
        @media print {
            .no-print { display: none !important; }
        }
        .ip-box {
            background: linear-gradient(135deg, #E9F0FB 0%, #DDE9F7 52%, #CCDFEF 100%);
            border: 1px solid #AECBE6;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 3px 14px rgba(0, 78, 166, 0.14);
        }
        .btn-primary {
            background-color: var(--fiea-azul);
            border-color: var(--fiea-azul);
            font-weight: 500;
        }
        .btn-primary:hover, .btn-primary:focus {
            background-color: var(--fiea-azul-escuro);
            border-color: var(--fiea-azul-escuro);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            vertical-align: middle;
        }
        .status-dot.online {
            background-color: var(--sucesso);
            box-shadow: 0 0 0 2px rgba(5, 150, 105, 0.2);
        }
        .status-dot.offline {
            background-color: var(--texto-dica);
        }
        .status-dot.warning {
            background-color: var(--alerta);
            box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.2);
        }
        .badge-localizado {
            background-color: var(--sucesso-fundo);
            color: var(--sucesso-texto);
            border: 1px solid #86EFAC;
            font-weight: 600;
        }
        .badge-fora-local {
            background-color: var(--alerta-fundo);
            color: var(--alerta-texto);
            border: 1px solid #FDE68A;
            font-weight: 600;
        }
        .badge-nao-localizado {
            background-color: var(--erro-fundo);
            color: var(--erro-texto);
            border: 1px solid #FECACA;
            font-weight: 600;
        }
        .badge-sem-cobertura {
            background-color: var(--fundo);
            color: var(--texto-secundario);
            border: 1px solid var(--contorno-forte);
            font-weight: 600;
        }
        .badge-sobras {
            background-color: var(--ciano-fundo);
            color: var(--ciano-texto);
            border: 1px solid #A5F3FC;
            font-weight: 600;
        }
        .badge-metal {
            background-color: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            font-weight: 500;
        }

    </style>
</head>
<body>
    <nav class="navbar navbar-expand-lg navbar-fiea mb-4">
        <div class="container-fluid px-4 pagina-container">
            <a class="navbar-brand d-flex align-items-center" href="/">
                <img src="/static/logo_fiea.jpg" alt="FIEA" width="45" height="45" class="me-2 rounded">
                Inventário
            </a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="nav-pill-btn">Início</a>
                <a href="/bens" class="nav-pill-btn">Bens Cadastrados</a>
                <a href="/salas" class="nav-pill-btn active">Gerenciar Salas</a>
                <a href="/relatorio" class="nav-pill-btn">Relatório de Inventário</a>
                <span class="badge-pill-status ms-2"><span class="dot-pulse"></span>Servidor Ativo</span>
            </div>
        </div>
    </nav>

    <div class="container-fluid px-4 pagina-container">
        <!-- Banner de Conexão com o Urovo -->
        <div class="ip-box mb-4 d-flex flex-wrap justify-content-between align-items-center gap-3">
            <div>
                <div class="d-flex align-items-center gap-2 mb-1">
                    <h6 class="text-primary mb-0 fw-bold">Cadastro e Gestão de Salas Físicas</h6>
                    <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 fw-medium" style="font-size: 0.76rem;" data-bs-toggle="modal" data-bs-target="#modalComoConectarSalas">
                        Como Conectar o Leitor?
                    </button>
                </div>
                <p class="mb-0 text-muted small d-flex flex-wrap align-items-center gap-2">
                    <span>Token de Conexão:</span>
                    <strong class="text-dark small font-monospace px-2 py-1 bg-white rounded border campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</strong>
                    <button type="button" class="btn btn-link btn-sm p-0 text-secondary btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                        <svg class="icone-olho-aberto" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                        <svg class="icone-olho-fechado" style="display:none;" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                    </button>
                </p>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div id="badgeColetor" class="chip chip-off">
                    <span id="dotColetor" class="status-dot offline"></span>
                    <span id="textoColetor">Coletor: Aguardando Wi-Fi</span>
                </div>
                <div class="text-end">
                    <span class="chip-contador">Total de Salas: {{ salas|length }}</span>
                </div>
            </div>
        </div>

        <!-- Modal Didático: Como Conectar o Leitor Urovo DT50 (Salas) -->
        <div class="modal fade modal-conexao-ajuda" id="modalComoConectarSalas" tabindex="-1" aria-labelledby="modalComoConectarSalasLabel" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content border-0 shadow">
                    <div class="modal-header bg-light">
                        <h6 class="modal-title fw-bold text-primary" id="modalComoConectarSalasLabel">
                            Como Conectar o Leitor Urovo DT50 ao Servidor
                        </h6>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fechar"></button>
                    </div>
                    <div class="modal-body p-4">
                        <ol class="list-group list-group-numbered list-group-flush mb-3">
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Conecte o Leitor na Mesma Rede Wi-Fi</div>
                                    <span class="text-muted small">O leitor Urovo DT50 e este computador precisam estar na mesma rede.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Abra a Configuração no Leitor</div>
                                    <span class="text-muted small">No menu lateral do aplicativo Urovo, toque em <strong>Configurações</strong> ou na barra de conexão no topo.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 1: Token Rápido (Recomendada)</div>
                                    <span class="text-muted small">Digite diretamente os 8 caracteres (sem hífen):</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-dark fs-5 campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 2: Conexão Direta (Endereço Completo)</div>
                                    <span class="text-muted small">Se preferir digitar o endereço completo, clique no olho para revelar:</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-primary fs-6 campo-endereco-mascaravel" data-real="http://{{ ip_local }}:5000" data-mascarado="••••••••••••••••" style="letter-spacing: 1px;">••••••••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-endereco" title="Mostrar ou ocultar endereço completo" aria-label="Mostrar ou ocultar endereço completo">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Toque em "Salvar e testar"</div>
                                    <span class="text-muted small">As salas cadastradas nesta página aparecem imediatamente na lista de seleção do leitor Urovo.</span>
                                </div>
                            </li>
                        </ol>
                    </div>
                    <div class="modal-footer bg-light py-2">
                        <button type="button" class="btn btn-primary btn-sm px-4" data-bs-dismiss="modal">Entendi</button>
                    </div>
                </div>
            </div>
        </div>

        {% if mensagem %}
        <div class="alert alert-{{ tipo_msg or 'info' }} alert-dismissible fade show" role="alert">
            {{ mensagem }}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Fechar"></button>
        </div>
        {% endif %}

        <div class="row g-4">
            <!-- Formulário de Cadastro de Sala -->
            <div class="col-12">
                <div class="card">
                    <div class="card-header py-3 d-flex justify-content-between align-items-center gap-3 flex-wrap">
                        <div class="d-flex align-items-center gap-3">
                            <span class="card-header-icon">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <circle cx="12" cy="12" r="9"></circle>
                                    <path d="M12 8v8M8 12h8"></path>
                                </svg>
                            </span>
                            <div>
                                <h6 class="mb-0 fw-bold">Cadastrar Nova Sala</h6>
                                <div class="small text-muted">Salas cadastradas aqui passam a aparecer na lista de seleção do leitor Urovo.</div>
                            </div>
                        </div>
                        <span class="chip-neutro">Cadastro manual</span>
                    </div>
                    <div class="card-body">
                        <form method="POST" action="/salas/cadastrar" id="formNovaSala" class="row g-3 align-items-start form-card">
                            <div class="col-12 col-md-4">
                                <label class="form-label small fw-semibold">Nome da Sala *</label>
                                <input type="text" name="nome" id="campoNomeSala" class="form-control form-control-lg" placeholder="Ex: EASY, Lab 1, Almoxarifado" required autofocus>
                                <div class="form-text small">Nome exato como será selecionado no inventário.</div>
                            </div>
                            <div class="col-12 col-md-4">
                                <label class="form-label small fw-semibold">Unidade *</label>
                                <input type="text" name="unidade" id="campoUnidadeSala" class="form-control form-control-lg" placeholder="Ex: UFAL, SENAI, FIEA" required>
                                <div class="form-text small">Unidade ou local à qual a sala pertence.</div>
                            </div>
                            <div class="col-12 col-md-4">
                                <label class="form-label small fw-semibold d-none d-md-block" aria-hidden="true">&nbsp;</label>
                                <button type="submit" class="btn btn-fiea btn-lg w-100">Salvar Sala</button>
                            </div>
                            <div class="col-12 d-flex align-items-center justify-content-between flex-wrap gap-2 divider-suave pt-3">
                                <button type="button" id="btnLimparFormNovaSala" class="btn btn-link btn-sm limpar-link text-decoration-none px-0">Limpar campos</button>
                                <a href="/bens" class="text-muted small text-decoration-none">&larr; Voltar para Cadastro de Bens</a>
                            </div>
                        </form>
                    </div>
                </div>
            </div>

            <!-- Tabela de Salas Cadastradas -->
            <div class="col-12">
                <div class="card">
                    <div class="card-header py-3 d-flex justify-content-between align-items-center gap-3 flex-wrap">
                        <div class="d-flex align-items-center gap-3">
                            <span class="card-header-icon">
                                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                                    <polyline points="9 22 9 12 15 12 15 22"></polyline>
                                </svg>
                            </span>
                            <div>
                                <h6 class="mb-0 fw-bold">Salas Cadastradas</h6>
                                <div class="small text-muted">Ambientes físicos e a quantidade de bens vinculados a cada um.</div>
                            </div>
                        </div>
                        <span class="chip-contador">{{ salas|length }} {% if salas|length == 1 %}sala{% else %}salas{% endif %}</span>
                    </div>
                    <div class="card-body p-0">
                        <div class="table-responsive" style="max-height: 520px; overflow-y: auto;">
                            <table class="table table-hover align-middle mb-0 tabela-cadastro">
                                <thead class="table-light small">
                                    <tr>
                                        <th>Nome da Sala</th>
                                        <th>Unidade</th>
                                        <th>Bens Vinculados</th>
                                        <th>Varreduras</th>
                                        <th class="text-end">Ações</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {% for s in salas %}
                                    <tr>
                                        <td class="fw-bold">{{ s.nome }}</td>
                                        <td><span class="chip-sala">{{ s.unidade }}</span></td>
                                        <td>
                                            {% if s.total_bens > 0 %}
                                                <span class="chip-contador">{{ s.total_bens }} {% if s.total_bens == 1 %}bem{% else %}bens{% endif %}</span>
                                            {% else %}
                                                <span class="text-muted small">Nenhum</span>
                                            {% endif %}
                                        </td>
                                        <td>
                                            {% if s.total_varreduras > 0 %}
                                                <span class="chip-neutro">{{ s.total_varreduras }} {% if s.total_varreduras == 1 %}varredura{% else %}varreduras{% endif %}</span>
                                            {% else %}
                                                <span class="text-muted small">Nenhuma</span>
                                            {% endif %}
                                        </td>
                                        <td class="text-end">
                                            {% if s.total_bens > 0 or s.total_varreduras > 0 %}
                                                <span class="d-inline-block" tabindex="0" title="Não pode ser excluída: possui {% if s.total_bens > 0 %}{{ s.total_bens }} bem(ns){% endif %}{% if s.total_bens > 0 and s.total_varreduras > 0 %} e {% endif %}{% if s.total_varreduras > 0 %}{{ s.total_varreduras }} varredura(s){% endif %} associada(s)." style="cursor: not-allowed;">
                                                    <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-2 disabled" tabindex="-1" style="pointer-events: none; opacity: 0.45;">
                                                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                            <polyline points="3 6 5 6 21 6"></polyline>
                                                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                                        </svg>
                                                    </button>
                                                </span>
                                            {% else %}
                                                <form method="POST" action="/salas/excluir/{{ s.id }}" onsubmit="return confirm('Deseja excluir a sala {{ s.nome }}?');" style="display:inline;">
                                                    <button type="submit" class="btn btn-sm btn-outline-danger py-0 px-2" title="Excluir Sala">
                                                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                                            <polyline points="3 6 5 6 21 6"></polyline>
                                                            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                                        </svg>
                                                    </button>
                                                </form>
                                            {% endif %}
                                        </td>
                                    </tr>
                                    {% else %}
                                    <tr>
                                        <td colspan="5" class="text-center py-5">
                                            <div class="text-muted mb-2">
                                                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="opacity-50">
                                                    <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"></path>
                                                    <polyline points="9 22 9 12 15 12 15 22"></polyline>
                                                </svg>
                                            </div>
                                            <h6 class="fw-bold mb-1">Nenhuma sala cadastrada ainda</h6>
                                            <p class="text-muted small mb-0">Cadastre a primeira sala acima (ex: Laboratório, Almoxarifado) para que o leitor Urovo possa listá-la.</p>
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

    <footer class="rodape no-print">
        <div class="container-fluid px-4 pagina-container py-3 d-flex justify-content-between align-items-center flex-wrap gap-2 small text-muted">
            <span><strong class="text-secondary">FIEA Inventário RFID</strong> · Servidor local nesta máquina</span>
            <span>Dados armazenados em <code>dados/inventario.db</code></span>
        </div>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Monitoramento em tempo real do status de conexão com o coletor Urovo
        // Monitoramento e alternância de máscara (Token e Endereço Completo)
        let tokenVisivel = false;
        let enderecoVisivel = false;

        function alternarMascaraToken() {
            tokenVisivel = !tokenVisivel;
            document.querySelectorAll('.campo-token-mascaravel').forEach(el => {
                el.innerText = tokenVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-token').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = tokenVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = tokenVisivel ? 'inline-block' : 'none';
                }
            });
        }

        function alternarMascaraEndereco() {
            enderecoVisivel = !enderecoVisivel;
            document.querySelectorAll('.campo-endereco-mascaravel').forEach(el => {
                el.innerText = enderecoVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = enderecoVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = enderecoVisivel ? 'inline-block' : 'none';
                }
            });
        }

        document.querySelectorAll('.btn-toggle-token').forEach(btn => {
            btn.addEventListener('click', alternarMascaraToken);
        });
        document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
            btn.addEventListener('click', alternarMascaraEndereco);
        });

        // Ao fechar qualquer modal de conexão/ajuda, reoculta o endereço completo por privacidade
        document.querySelectorAll('.modal-conexao-ajuda').forEach(modalEl => {
            modalEl.addEventListener('hidden.bs.modal', () => {
                if (enderecoVisivel) {
                    alternarMascaraEndereco();
                }
            });
        });

        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        if (data.fila_pendente && data.fila_pendente > 0) {
                            badge.className = 'chip chip-warn';
                            dot.className = 'status-dot warning';
                            texto.innerText = `Coletor Conectado · ${data.fila_pendente} pendência(s) na fila`;
                        } else {
                            badge.className = 'chip chip-ok';
                            dot.className = 'status-dot online';
                            const tempo = (data.segundos_atras !== null && data.segundos_atras !== undefined) ? `há ${data.segundos_atras}s` : 'ativo';
                            texto.innerText = `Coletor Conectado`;
                        }
                    } else {
                        badge.className = 'chip chip-off';
                        dot.className = 'status-dot offline';
                        texto.innerText = 'Coletor: Aguardando Wi-Fi';
                    }
                })
                .catch(() => {});
        }
        setInterval(atualizarStatusColetor, 3000);
        atualizarStatusColetor();

        // Rascunho automático em sessionStorage para formulário de salas
        const CHAVE_RASCUNHO_SALA = 'rascunho_nova_sala';
        const campoNomeSala = document.getElementById('campoNomeSala');
        const campoUnidadeSala = document.getElementById('campoUnidadeSala');
        const formNovaSala = document.getElementById('formNovaSala');

        function salvarRascunhoSala() {
            const rascunho = {
                nome: campoNomeSala ? campoNomeSala.value : '',
                unidade: campoUnidadeSala ? campoUnidadeSala.value : ''
            };
            if (rascunho.nome || rascunho.unidade) {
                sessionStorage.setItem(CHAVE_RASCUNHO_SALA, JSON.stringify(rascunho));
            } else {
                sessionStorage.removeItem(CHAVE_RASCUNHO_SALA);
            }
        }

        function restaurarRascunhoSala() {
            {% if tipo_msg == 'success' %}
            sessionStorage.removeItem(CHAVE_RASCUNHO_SALA);
            return;
            {% endif %}

            const salvo = sessionStorage.getItem(CHAVE_RASCUNHO_SALA);
            if (!salvo) return;
            try {
                const dados = JSON.parse(salvo);
                if (campoNomeSala && dados.nome !== undefined) {
                    campoNomeSala.value = dados.nome;
                }
                if (campoUnidadeSala && dados.unidade !== undefined) {
                    campoUnidadeSala.value = dados.unidade;
                }
            } catch (e) {
                sessionStorage.removeItem(CHAVE_RASCUNHO_SALA);
            }
        }

        if (campoNomeSala) campoNomeSala.addEventListener('input', salvarRascunhoSala);
        if (campoUnidadeSala) campoUnidadeSala.addEventListener('input', salvarRascunhoSala);
        if (formNovaSala) {
            formNovaSala.addEventListener('submit', function() {
                sessionStorage.removeItem(CHAVE_RASCUNHO_SALA);
            });
        }

        const btnLimparFormNovaSala = document.getElementById('btnLimparFormNovaSala');
        if (btnLimparFormNovaSala) {
            btnLimparFormNovaSala.addEventListener('click', function() {
                if (campoNomeSala) {
                    campoNomeSala.value = '';
                    campoNomeSala.focus();
                }
                if (campoUnidadeSala) {
                    campoUnidadeSala.value = '';
                }
                sessionStorage.removeItem(CHAVE_RASCUNHO_SALA);
            });
        }

        restaurarRascunhoSala();
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
    <link rel="icon" type="image/png" sizes="32x32" href="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAACy0lEQVR42u1X7UtTYRS/JVgL+lIfoj4U1Mc+9Af0oaToBXpHShalIBlUWF8KelMpi0KpoKiMQiOxZbJJmJVvm+415wtJbbaZuc1pbZPNuTXny073nMu9LPdSH5q3YAcO9z7P+e2c3/O757l3D8PwlpG5hFm78wyzsaid2VMDKXHMjTWw1i8mWb6aySozpazwXMdaWFNY+XwWjyZBSqAk812cd6yd0mf+Jz0hWnHe0wTSBP5JAvuvd4CsYwgM/W7QmV3Q2OWEI7d0wEgVwJR+4PxwPWFzyjTwUmMjrJ71N91O2HVFJeTKva2DBuMw6M1uqFZ9hWXSuuQEdpQoIRKJwFw78aATmNPtwFSNAlPeD0yOHA7eVMdgcZx/10C5Nl9oEeL89Vrtx+QEFHo7Ab2BMBTcew/HWMfrqjwFR+B4E2y93AoLWGy9wUFYpydIGB6/Jp9Tp07L5TI7fGB3Bej+/mtLcgKB0BQBUa7F2S/IFx2QUSz7hho+2XygMbkgk53z/+Cw+AgkLE4ShV1xVA4ToWmKl9T0QffAGN1XvLUmJoAJoiXj/fPwOK3Y4Q5S7HHTAKw/2RAXa7R4KNepCqMwtzJXDkarh7APkxHYdL457vPHgusKXgnjYnZFWRdb4mJROcxlHfHT2MZKv/uqCixO/+8VOFfVK6xoQ2GjIGvGvufU7fyKthW1wdnKnoRYHMdTB+1p62BiAsgO7bs3RJJHxwofdQkJlx6qpW2KNjsbgbw7epCWa8lxmz1hFUMLTk5DW9838vEg1y9ynT0xgXc9IwQyWsdi3g2VLV8oFgrP0LiU3U7RW4/3LZdawTsRpnl8fyAWF8P3QHPvaGICWra7TXYflMlNMQRwy2HsmZKTEFVAxVCtyakZ2radbANuL24jHDr2Cf97mXqI5qqVg3//Vbxwb/pjlCbwXxMQ/W+56AcT0Y9moh9ORT6e/wQsXsMsZqQz6gAAAABJRU5ErkJggg==">
    <link rel="icon" type="image/png" sizes="32x32" href="/favicon.png?v=fiea">
    <link rel="icon" type="image/x-icon" href="/favicon.ico?v=fiea">
    <link rel="shortcut icon" href="/favicon.ico?v=fiea">
    <link rel="apple-touch-icon" href="/favicon.png?v=fiea">
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        :root {
            --fiea-azul: #004EA6;
            --fiea-azul-escuro: #003A7C;
            --fiea-azul-claro: #00A0E7;
            --fiea-azul-fundo: #E6EEF8;
            --fundo: #F1F5F9;
            --superficie: #FFFFFF;
            --contorno: #E2E8F0;
            --contorno-forte: #CBD5E1;
            --texto-primario: #1E293B;
            --texto-secundario: #475569;
            --texto-dica: #64748B;
            --sucesso: #059669;
            --sucesso-fundo: #DCFCE7;
            --sucesso-texto: #166534;
            --alerta: #D97706;
            --alerta-fundo: #FEF3C7;
            --alerta-texto: #92400E;
            --erro: #DC2626;
            --erro-fundo: #FEE2E2;
            --erro-texto: #991B1B;
            --info: #0284C7;
            --info-fundo: #E0F2FE;
            --info-texto: #075985;
            --ciano: #0891B2;
            --ciano-fundo: #CFFAFE;
            --ciano-texto: #155E75;
        }
        body {
            background: linear-gradient(180deg, #E2E8F0 0%, var(--fundo) 320px);
            font-family: system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            color: var(--texto-primario);
            -webkit-font-smoothing: antialiased;
        }
        .navbar-fiea {
            background: linear-gradient(100deg, #003264 0%, var(--fiea-azul-escuro) 28%, var(--fiea-azul) 75%, #11599F 100%);
            border-bottom: 1px solid var(--fiea-azul-escuro);
        }
        .navbar-brand {
            font-weight: 700;
            letter-spacing: -0.01em;
            color: #FFFFFF !important;
            font-size: 1.25rem;
        }
        .nav-pill-btn {
            border-radius: 8px;
            padding: 6px 14px;
            font-size: 0.875rem;
            text-decoration: none;
            transition: all 0.15s ease-in-out;
            display: inline-flex;
            align-items: center;
        }
        .nav-pill-btn.active {
            background-color: #FFFFFF;
            color: var(--fiea-azul) !important;
            font-weight: 600;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }
        .nav-pill-btn:not(.active) {
            background-color: rgba(255, 255, 255, 0.12);
            color: #FFFFFF !important;
        }
        .nav-pill-btn:not(.active):hover {
            background-color: rgba(255, 255, 255, 0.22);
        }
        .card {
            border-radius: 12px;
            border: 1px solid var(--contorno);
            background-color: var(--superficie);
            box-shadow: 0 3px 18px rgba(16, 42, 67, 0.10);
        }
        .card-header {
            background-color: #F3F8FE;
            border-bottom: 1px solid #DFE9F6;
        }
        .card-header-icon {
            width: 40px;
            height: 40px;
            border-radius: 10px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: var(--fiea-azul-fundo);
            color: var(--fiea-azul);
            flex: 0 0 auto;
        }
        .tabela-cadastro thead th {
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.06em;
            color: #3D5068;
            white-space: nowrap;
            background: #E8EEF7;
            border-bottom: 2px solid #CFDCEC;
        }
        .tabela-cadastro tbody td {
            padding-top: 0.85rem;
            padding-bottom: 0.85rem;
            border-bottom-color: #ECF1F8;
        }
        .tabela-cadastro tbody tr:nth-child(even) {
            background-color: #FAFCFF;
        }
        .tabela-cadastro tbody tr:hover {
            background-color: #E8F0FB;
        }
        .pagina-container {
            max-width: 1240px;
            margin-left: auto;
            margin-right: auto;
        }
        /* Formulário de cadastro — polimento visual */
        .form-card { max-width: 1040px; }
        .form-card .form-label { color: #33415C; letter-spacing: 0.02em; }
        .form-card .form-control-lg,
        .form-card .form-select-lg {
            border-color: #D3DEEA;
            border-radius: 10px;
            color: #1E293B;
        }
        .form-card .form-control-lg:focus,
        .form-card .form-select-lg:focus {
            border-color: var(--fiea-azul);
            box-shadow: 0 0 0 3px rgba(0, 78, 166, 0.15);
        }
        .form-card .form-control-lg::placeholder { color: #A8B7CB; }
        .form-card .form-text { color: #64748B; }
        .form-card .input-group-lg .form-control { border-radius: 10px 0 0 10px; border-color: #D3DEEA; }
        .form-card .input-group-lg .btn-outline-secondary {
            border-color: #D3DEEA;
            border-left: none;
            border-radius: 0 10px 10px 0;
            background: #F1F5F9;
            color: #3D4B5F;
            font-weight: 600;
        }
        .form-card .input-group-lg .btn-outline-secondary:hover,
        .form-card .input-group-lg .btn-outline-secondary:focus {
            background: #E2E8F0;
            color: #1E293B;
            border-color: #CBD5E1;
        }
        .btn-fiea {
            background: linear-gradient(180deg, #0F63B4 0%, var(--fiea-azul) 55%, #003A7C 140%);
            border: 1px solid var(--fiea-azul-escuro);
            box-shadow: 0 6px 16px rgba(0, 78, 166, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.22);
            color: #FFFFFF;
            font-weight: 600;
            letter-spacing: 0.01em;
        }
        .btn-fiea:hover,
        .btn-fiea:focus {
            background: linear-gradient(180deg, #0B5CB8 0%, var(--fiea-azul-escuro) 120%);
            border-color: var(--fiea-azul-escuro);
            color: #FFFFFF;
        }
        .limpar-link { color: #64748B; font-weight: 500; }
        .limpar-link:hover { color: var(--fiea-azul); }
        .metal-card {
            background: linear-gradient(180deg, #F0F7FF 0%, #E9F0FB 100%);
            border: 1px solid #C4D8EE;
            border-radius: 12px;
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }
        .metal-card:hover { border-color: #A9C8E8; box-shadow: 0 2px 10px rgba(0, 78, 166, 0.12); }
        .divider-suave { border-top: 1px solid #E4ECF7; }
        .tag-mono {
            display: inline-flex;
            align-items: center;
            background: #EDF2F9;
            color: #16324F;
            border: 1px solid #C9D8EA;
            border-radius: 6px;
            padding: 3px 8px;
            font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
            font-size: 0.76rem;
            font-weight: 600;
            letter-spacing: 0.02em;
        }
        .chip-contador {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: var(--fiea-azul-fundo);
            color: var(--fiea-azul-escuro);
            border: 1px solid #BFD5EE;
            border-radius: 999px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 4px 12px;
            white-space: nowrap;
        }
        .chip-sala {
            display: inline-flex;
            align-items: center;
            background: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .chip-alerta {
            display: inline-flex;
            align-items: center;
            background: var(--alerta-fundo);
            color: var(--alerta-texto);
            border: 1px solid #FCD34D;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .chip-neutro {
            display: inline-flex;
            align-items: center;
            background: #ECEFF4;
            color: #3D4B5F;
            border: 1px solid #D3DAE4;
            border-radius: 6px;
            font-weight: 600;
            font-size: 0.8rem;
            padding: 3px 10px;
        }
        .badge-pill-status {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(52, 211, 153, 0.16);
            color: #A7F3D0;
            border: 1px solid rgba(52, 211, 153, 0.45);
            font-size: 0.78rem;
            font-weight: 600;
            padding: 6px 14px;
            border-radius: 999px;
            letter-spacing: 0.03em;
            white-space: nowrap;
        }
        .dot-pulse {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #34D399;
            animation: dotPulse 1.7s ease-out infinite;
        }
        @keyframes dotPulse {
            0% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0.55); }
            70% { box-shadow: 0 0 0 7px rgba(52, 211, 153, 0); }
            100% { box-shadow: 0 0 0 0 rgba(52, 211, 153, 0); }
        }
        @media (prefers-reduced-motion: reduce) {
            .dot-pulse { animation: none; }
        }
        .chip {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 16px;
            border-radius: 999px;
            font-size: 0.86rem;
            font-weight: 600;
            border: 1px solid transparent;
            transition: background 0.2s ease, color 0.2s ease, border-color 0.2s ease;
            width: max-content;
        }
        .chip-ok { background: var(--sucesso-fundo); color: var(--sucesso-texto); border-color: #86EFAC; }
        .chip-warn { background: var(--alerta-fundo); color: var(--alerta-texto); border-color: #FCD34D; }
        .chip-off { background: #E9EDF3; color: #33415C; border-color: #C9D3E0; }
        .rodape {
            border-top: 1px solid var(--contorno);
            background: #FFFFFF;
        }
        @media print {
            .no-print { display: none !important; }
        }
        .ip-box {
            background: linear-gradient(135deg, #E9F0FB 0%, #DDE9F7 52%, #CCDFEF 100%);
            border: 1px solid #AECBE6;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 3px 14px rgba(0, 78, 166, 0.14);
        }
        .btn-primary {
            background-color: var(--fiea-azul);
            border-color: var(--fiea-azul);
            font-weight: 500;
        }
        .btn-primary:hover, .btn-primary:focus {
            background-color: var(--fiea-azul-escuro);
            border-color: var(--fiea-azul-escuro);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            display: inline-block;
            vertical-align: middle;
        }
        .status-dot.online {
            background-color: var(--sucesso);
            box-shadow: 0 0 0 2px rgba(5, 150, 105, 0.2);
        }
        .status-dot.offline {
            background-color: var(--texto-dica);
        }
        .status-dot.warning {
            background-color: var(--alerta);
            box-shadow: 0 0 0 2px rgba(217, 119, 6, 0.2);
        }
        .badge-localizado {
            background-color: var(--sucesso-fundo);
            color: var(--sucesso-texto);
            border: 1px solid #86EFAC;
            font-weight: 600;
        }
        .badge-fora-local {
            background-color: var(--alerta-fundo);
            color: var(--alerta-texto);
            border: 1px solid #FDE68A;
            font-weight: 600;
        }
        .badge-nao-localizado {
            background-color: var(--erro-fundo);
            color: var(--erro-texto);
            border: 1px solid #FECACA;
            font-weight: 600;
        }
        .badge-sem-cobertura {
            background-color: var(--fundo);
            color: var(--texto-secundario);
            border: 1px solid var(--contorno-forte);
            font-weight: 600;
        }
        .badge-indeterminado {
            background-color: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            font-weight: 600;
        }
        .badge-sobras {
            background-color: var(--ciano-fundo);
            color: var(--ciano-texto);
            border: 1px solid #A5F3FC;
            font-weight: 600;
        }
        .badge-metal {
            background-color: var(--info-fundo);
            color: var(--info-texto);
            border: 1px solid #BAE6FD;
            font-weight: 500;
        }

        .kpi-card { border-radius: 12px; transition: transform 0.15s ease, box-shadow 0.15s ease; }
        .kpi-card:hover { transform: translateY(-3px); box-shadow: 0 12px 28px rgba(16, 42, 67, 0.14); }
        
        /* Área de Arraste de Arquivos (Drag and Drop) */
        .drop-zone {
            border: 2px dashed #004EA6;
            border-radius: 12px;
            background-color: var(--fiea-azul-fundo);
            transition: all 0.2s ease-in-out;
            cursor: pointer;
        }
        .drop-zone.dragover {
            background-color: #DBEAFE;
            border-color: #003A7C;
            transform: scale(1.01);
        }

        /* Estilização para Impressão / Exportação PDF */
        @media print {
            /* Conteúdo recolhido na tela imprime completo no papel */
            .collapse {
                display: block !important;
                height: auto !important;
            }
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
    <nav class="navbar navbar-expand-lg navbar-fiea mb-4 no-print">
        <div class="container-fluid px-4 pagina-container">
            <a class="navbar-brand d-flex align-items-center" href="/">
                <img src="/static/logo_fiea.jpg" alt="FIEA" width="45" height="45" class="me-2 rounded">
                Inventário
            </a>
            <div class="d-flex align-items-center gap-2">
                <a href="/" class="nav-pill-btn">Início</a>
                <a href="/bens" class="nav-pill-btn">Bens Cadastrados</a>
                <a href="/salas" class="nav-pill-btn">Gerenciar Salas</a>
                <a href="/relatorio" class="nav-pill-btn active">Relatório de Inventário</a>
                <span class="badge-pill-status ms-2"><span class="dot-pulse"></span>Servidor Ativo</span>
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
                <strong>Data da Auditoria:</strong> {{ sessao_inicio_fmt }}<br>
                <strong>Data de Emissão do Relatório:</strong> <span class="data-emissao-print"></span><br>
                <strong>Total de Varreduras:</strong> {{ varreduras|length }} {% if varreduras|length == 1 %}sala auditada{% else %}salas auditadas{% endif %}
            </div>
        </div>
    </div>

    <div class="container-fluid px-4 pagina-container">
        <!-- Banner de Conexão com o Urovo (No-print) -->
        <div class="ip-box mb-4 d-flex flex-wrap justify-content-between align-items-center gap-3 no-print">
            <div>
                <div class="d-flex align-items-center gap-2 mb-1">
                    <h6 class="text-primary mb-0 fw-bold">Relatório de Conferência e Auditoria Patrimonial</h6>
                    <button type="button" class="btn btn-sm btn-outline-primary py-0 px-2 fw-medium" style="font-size: 0.76rem;" data-bs-toggle="modal" data-bs-target="#modalComoConectarRelatorio">
                        Como Conectar o Leitor?
                    </button>
                </div>
                <p class="mb-0 text-muted small d-flex flex-wrap align-items-center gap-2">
                    <span>Token de Conexão:</span>
                    <strong class="text-dark small font-monospace px-2 py-1 bg-white rounded border campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</strong>
                    <button type="button" class="btn btn-link btn-sm p-0 text-secondary btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                        <svg class="icone-olho-aberto" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                        <svg class="icone-olho-fechado" style="display:none;" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                    </button>
                </p>
            </div>
            <div class="d-flex align-items-center gap-3">
                <div id="badgeColetor" class="chip chip-off">
                    <span id="dotColetor" class="status-dot offline"></span>
                    <span id="textoColetor">Coletor: Aguardando Wi-Fi</span>
                </div>
                <div class="text-end">
                    <span class="chip-contador">Sessões: {{ sessoes|length }}</span>
                </div>
            </div>
        </div>

        <!-- Modal Didático: Como Conectar o Leitor Urovo DT50 (Relatório) -->
        <div class="modal fade modal-conexao-ajuda" id="modalComoConectarRelatorio" tabindex="-1" aria-labelledby="modalComoConectarRelatorioLabel" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content border-0 shadow">
                    <div class="modal-header bg-light">
                        <h6 class="modal-title fw-bold text-primary" id="modalComoConectarRelatorioLabel">
                            Como Conectar o Leitor Urovo DT50 ao Servidor
                        </h6>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Fechar"></button>
                    </div>
                    <div class="modal-body p-4">
                        <ol class="list-group list-group-numbered list-group-flush mb-3">
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Conecte o Leitor na Mesma Rede Wi-Fi</div>
                                    <span class="text-muted small">O leitor Urovo DT50 e este computador precisam estar na mesma rede.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Abra a Configuração no Leitor</div>
                                    <span class="text-muted small">No aplicativo do Urovo, toque na barra de conexão no topo ou no menu lateral em <strong>Configurações</strong>.</span>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 1: Token Rápido (Recomendada)</div>
                                    <span class="text-muted small">Digite diretamente os 8 caracteres (sem hífen):</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-dark fs-5 campo-token-mascaravel" data-real="{{ token_conexao }}" data-mascarado="••••••••••" style="letter-spacing: 1px;">••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-token" title="Mostrar ou ocultar token" aria-label="Mostrar ou ocultar token">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto w-100">
                                    <div class="fw-bold">Opção 2: Conexão Direta (Endereço Completo)</div>
                                    <span class="text-muted small">Se preferir digitar o endereço completo, clique no olho para revelar:</span>
                                    <div class="mt-2 p-2 bg-light rounded border d-flex align-items-center justify-content-between">
                                        <span class="font-monospace fw-bold text-primary fs-6 campo-endereco-mascaravel" data-real="http://{{ ip_local }}:5000" data-mascarado="••••••••••••••••" style="letter-spacing: 1px;">••••••••••••••••</span>
                                        <button type="button" class="btn btn-outline-secondary btn-sm py-1 px-2 btn-toggle-endereco" title="Mostrar ou ocultar endereço completo" aria-label="Mostrar ou ocultar endereço completo">
                                            <svg class="icone-olho-aberto" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
                                            <svg class="icone-olho-fechado" style="display:none;" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>
                                        </button>
                                    </div>
                                </div>
                            </li>
                            <li class="list-group-item d-flex align-items-start gap-2 border-0 px-0">
                                <div class="ms-2 me-auto">
                                    <div class="fw-bold">Toque em "Salvar e testar"</div>
                                    <span class="text-muted small">Ao concluir uma varredura no leitor, toque em "Sincronizar" para enviar os dados instantaneamente para esta página.</span>
                                </div>
                            </li>
                        </ol>
                    </div>
                    <div class="modal-footer bg-light py-2">
                        <button type="button" class="btn btn-primary btn-sm px-4" data-bs-dismiss="modal">Entendi</button>
                    </div>
                </div>
            </div>
        </div>

        {% if mensagem %}
        <div class="alert alert-{{ tipo_msg or 'info' }} alert-dismissible fade show no-print" role="alert">
            {{ mensagem }}
            <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Fechar"></button>
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
                                <button type="submit" class="btn btn-link text-danger p-0 text-decoration-none small d-inline-flex align-items-center gap-1" style="font-size: 0.78rem;" title="Excluir esta sessão de inventário">
                                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                        <polyline points="3 6 5 6 21 6"></polyline>
                                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                                    </svg>
                                    Excluir esta Sessão
                                </button>
                            </form>
                            {% endif %}
                        </div>
                        <select class="form-select form-select-sm fw-semibold" onchange="window.location.href='/relatorio?sessao=' + this.value">
                            {% for s in sessoes %}
                            <option value="{{ s.id }}" {% if s.id == sessao_id %}selected{% endif %}>
                                Sessão {{ s.id }} — {{ formatar_data_hora(s.inicio) }} (Op: {{ s.operador or 'N/D' }} · {{ s.total_varreduras }} varreduras)
                            </option>
                            {% else %}
                            <option value="" disabled selected>Nenhuma sessão cadastrada ainda</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="col-lg-7 col-md-12 d-flex justify-content-lg-end gap-2 pt-lg-3">
                        <button type="button" class="btn btn-sm btn-outline-primary d-inline-flex align-items-center gap-1" onclick="toggleAreaImportacao()">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="17 8 12 3 7 8"></polyline>
                                <line x1="12" y1="3" x2="12" y2="15"></line>
                            </svg>
                            Importar CSVs Offline
                        </button>
                        {% if sessao_id %}
                        <a href="/relatorio/{{ sessao_id }}/exportar-csv" class="btn btn-sm btn-outline-success d-inline-flex align-items-center gap-1">
                            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            Baixar CSV / Excel
                        </a>
                        <div class="d-inline-flex align-items-center bg-light border rounded px-2 py-1">
                            <div class="form-check form-check-inline mb-0 me-2 small">
                                <input class="form-check-input" type="checkbox" id="checkImprimirSobras" checked onchange="toggleImprimirSobras(this.checked)">
                                <label class="form-check-label text-muted small" for="checkImprimirSobras" title="Marque para incluir a lista de tags não cadastradas no PDF impresso">Incluir Sobras no PDF</label>
                            </div>
                            <button type="button" class="btn btn-sm btn-primary d-inline-flex align-items-center gap-1" onclick="imprimirRelatorio()" title="Gera o PDF oficial através do diálogo de impressão do navegador">
                                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                    <polyline points="6 9 6 2 18 2 18 9"></polyline>
                                    <path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"></path>
                                    <rect x="6" y="14" width="12" height="8"></rect>
                                </svg>
                                Exportar PDF / Imprimir
                            </button>
                        </div>
                        {% endif %}
                    </div>
                </div>
            </div>
        </div>

        <!-- Área de Importação (recolhida por padrão; clique no cabeçalho para expandir) -->
        <div class="card mb-4 no-print border-primary border-opacity-50" id="cardImportacao">
            <div class="card-header py-3 d-flex justify-content-between align-items-center gap-2" data-bs-toggle="collapse" data-bs-target="#corpoImportacao" role="button" tabindex="0" aria-expanded="false" aria-controls="corpoImportacao" style="cursor: pointer;">
                <div class="d-flex align-items-center gap-2">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#004EA6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                        <polyline points="17 8 12 3 7 8"></polyline>
                        <line x1="12" y1="3" x2="12" y2="15"></line>
                    </svg>
                    <div>
                        <h6 class="mb-0 fw-bold text-primary">Importação de Arquivos da Coleta Offline (Urovo DT50)</h6>
                        <small class="text-muted">Como enviar os arquivos sem confusão com nomes compridos</small>
                    </div>
                </div>
                <span class="btn btn-sm btn-outline-primary d-inline-flex align-items-center gap-1 fw-semibold" title="Expandir ou recolher a área de importação">
                    <span class="texto-toggle-importacao">Expandir</span>
                    <svg class="icone-chevron-importacao" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transition: transform 0.2s ease;"><polyline points="6 9 12 15 18 9"></polyline></svg>
                </span>
            </div>
            <div class="collapse" id="corpoImportacao">
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
                                Conecte o cabo USB do DT50 ao PC (modo "Transferência de Arquivos") e abra a pasta <code>XLSFile</code> (<code>/sdcard/XLSFile/</code>) no armazenamento interno.
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
                            <div class="mb-2">
                                <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#004EA6" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                    <polyline points="17 8 12 3 7 8"></polyline>
                                    <line x1="12" y1="3" x2="12" y2="15"></line>
                                </svg>
                            </div>
                            <h6 class="fw-bold mb-1">Arraste os arquivos CSV aqui ou clique para selecionar do PC</h6>
                            <p class="text-muted small mb-0">
                                Dica: Você pode arrastar <code>sessao_*.csv</code> e <code>leituras_*.csv</code> juntos. O sistema agrupa automaticamente.
                            </p>
                        </div>
                        <div id="fileListContainer" class="mt-3 text-start small d-none"></div>
                        <div id="btnContainer" class="mt-3 d-none">
                            <button type="submit" id="btnEnviarCsv" class="btn btn-success px-4 py-2 fw-semibold">
                                Processar Arquivos Selecionados e Atualizar Relatório
                            </button>
                        </div>
                    </div>
                </form>
            </div>
            </div>
        </div>

        {% if sessao_atual and relatorio %}
        <!-- Sessão Atual Recolhida por Padrão (No-print no cabeçalho; corpo imprime completo) -->
        <div class="card mb-4" id="cardSessaoAtual">
            <div class="card-header py-2 px-3 d-flex justify-content-between align-items-center gap-2" data-bs-toggle="collapse" data-bs-target="#corpoSessaoAtual" role="button" tabindex="0" aria-expanded="false" aria-controls="corpoSessaoAtual" style="cursor: pointer;">
                <div class="d-flex flex-wrap align-items-center gap-2 small">
                    <strong class="text-secondary text-uppercase" style="font-size: 0.68rem; letter-spacing: 0.08em;">Sessão atual</strong>
                    <span class="badge bg-primary">{{ sessao_atual.id }}</span>
                    <span class="badge bg-secondary">{{ sessao_atual.unidade }}</span>
                    <span class="chip-neutro">{{ sessao_atual.operador }}</span>
                    <span class="chip-neutro d-inline-flex align-items-center gap-1" title="Data e hora de início da sessão">
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="opacity: 0.75;"><rect x="3" y="4" width="18" height="18" rx="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>
                        {{ sessao_inicio_fmt }}
                    </span>
                    {% if sessao_atual.encerrada_auto %}
                    <span class="badge bg-warning text-dark border border-warning" title="Sessão finalizada automaticamente com base no término da última varredura">Encerramento automático</span>
                    {% elif sessao_fim_fmt == 'Em aberto' %}
                    <span class="badge bg-info text-dark border border-info">Em andamento</span>
                    {% endif %}
                </div>
                <span class="btn btn-sm btn-outline-secondary d-inline-flex align-items-center gap-1 fw-semibold no-print" title="Expandir ou recolher detalhes da sessão">
                    <span class="texto-toggle-sessao">Expandir</span>
                    <svg class="icone-chevron-sessao" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="transition: transform 0.2s ease;"><polyline points="6 9 12 15 18 9"></polyline></svg>
                </span>
            </div>
            <div class="collapse" id="corpoSessaoAtual">
                <div class="card-body p-3">
                    <div class="d-flex flex-wrap align-items-center gap-2 small text-muted mb-2 border-bottom pb-2">
                        <span><strong class="text-secondary">Início:</strong> {{ sessao_inicio_fmt }}</span>
                        <span>·</span>
                        <span><strong class="text-secondary">Fim:</strong> {{ sessao_fim_fmt }}</span>
                        {% if sessao_atual.duracao_s is not none and sessao_atual.duracao_s > 0 and sessao_fim_fmt != 'Em aberto' %}
                        <span>·</span>
                        <span><strong class="text-secondary">Duração:</strong> {{ formatar_duracao(sessao_atual.duracao_s) }}</span>
                        {% else %}
                        <span>·</span>
                        <span><strong class="text-secondary">Duração:</strong> <span class="badge bg-light text-primary border">Em andamento</span></span>
                        {% endif %}
                        {% if sessao_atual.encerrada_auto %}
                        <span>·</span>
                        <span class="badge bg-warning text-dark border border-warning">Encerramento automático</span>
                        {% endif %}
                    </div>
                    {% if sessao_atual.encerrada_auto %}
                    <div class="alert alert-warning py-1 px-2 d-flex align-items-center gap-2 mb-3 small text-dark" style="background-color: #fff9e6; border: 1px solid #ffe082; border-radius: 6px;">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#b45309" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="flex-shrink-0"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
                        <span><strong>Aviso de encerramento automático:</strong> Esta sessão não foi encerrada formalmente pelo operador no leitor de mão. O término e a duração foram calculados com base na última varredura realizada.</span>
                    </div>
                    {% endif %}
                    <div class="text-muted small fw-semibold mb-2">Varreduras realizadas nesta sessão:</div>
                    <div class="d-flex flex-wrap gap-1">
                        {% for v in varreduras %}
                        <span class="chip-sala">
                            Sala {{ v.sala or 'Não Identificada' }} (v{{ "%02d"|format(v.numero) }} · {{ v.total_etiquetas }} tags)
                        </span>
                        {% else %}
                        <span class="chip-neutro">Nenhuma varredura registrada</span>
                        {% endfor %}
                    </div>
                </div>
            </div>
        </div>

        <!-- Indicadores / Cards de KPI -->
        <div class="row g-3 mb-4">
            <!-- Total de Bens -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4 border-primary">
                    <div class="text-muted small fw-semibold text-uppercase">Total Esperado</div>
                    <div class="fs-2 fw-bold text-dark my-1">{{ relatorio.bens|length }}</div>
                    <div class="text-muted small">Bens no cadastro</div>
                </div>
            </div>
            <!-- Localizados -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4" style="border-left-color: var(--sucesso) !important;">
                    <div class="small fw-semibold text-uppercase d-flex align-items-center gap-1" style="color: var(--sucesso-texto);">
                        <span class="status-dot online"></span> Localizados
                    </div>
                    <div class="fs-2 fw-bold my-1" style="color: var(--sucesso-texto);">{{ localizados|length }}</div>
                    <div class="text-muted small">{{ pct_localizados }}% de acurácia</div>
                </div>
            </div>
            <!-- Fora do Local -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4" style="border-left-color: var(--alerta) !important;">
                    <div class="small fw-semibold text-uppercase d-flex align-items-center gap-1" style="color: var(--alerta-texto);">
                        <span class="status-dot" style="background-color: var(--alerta);"></span> Fora do Local
                    </div>
                    <div class="fs-2 fw-bold my-1" style="color: var(--alerta-texto);">{{ fora_do_local|length }}</div>
                    <div class="text-muted small">Sala trocada / divergente</div>
                </div>
            </div>
            <!-- Não Localizados -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4" style="border-left-color: var(--erro) !important;">
                    <div class="small fw-semibold text-uppercase d-flex align-items-center gap-1" style="color: var(--erro-texto);">
                        <span class="status-dot" style="background-color: var(--erro);"></span> Não Localizados
                    </div>
                    <div class="fs-2 fw-bold my-1" style="color: var(--erro-texto);">{{ nao_localizados|length }}</div>
                    <div class="text-muted small">Faltantes na sala</div>
                </div>
            </div>
            <!-- Sala Indeterminada: lido em mais de uma sala sem como dizer em qual está -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4" style="border-left-color: var(--info) !important;">
                    <div class="small fw-semibold text-uppercase d-flex align-items-center gap-1" style="color: var(--info-texto);">
                        <span class="status-dot" style="background-color: var(--info);"></span> Sala Indeterminada
                    </div>
                    <div class="fs-2 fw-bold my-1" style="color: var(--info-texto);">{{ indeterminados|length }}</div>
                    <div class="text-muted small">Procurar no local</div>
                </div>
            </div>
            <!-- Sem Cobertura -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4" style="border-left-color: var(--texto-secundario) !important;">
                    <div class="small fw-semibold text-uppercase d-flex align-items-center gap-1" style="color: var(--texto-secundario);">
                        <span class="status-dot offline"></span> Sem Cobertura
                    </div>
                    <div class="fs-2 fw-bold my-1" style="color: var(--texto-secundario);">{{ sem_cobertura|length }}</div>
                    <div class="text-muted small">Sala não auditada</div>
                </div>
            </div>
            <!-- Tags Sobrando -->
            <div class="col-xl col-md-4 col-6">
                <div class="card kpi-card bg-white h-100 p-3 border-start border-4" style="border-left-color: var(--ciano) !important;">
                    <div class="small fw-semibold text-uppercase d-flex align-items-center gap-1" style="color: var(--ciano-texto);">
                        <span class="status-dot" style="background-color: var(--ciano);"></span> Tags Sobrando
                    </div>
                    <div class="fs-2 fw-bold my-1" style="color: var(--ciano-texto);">{{ nao_cadastrados|length }}</div>
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
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="filtrarTabela('localizado', this)">
                            Localizados ({{ localizados|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="filtrarTabela('fora do local', this)">
                            Fora do Local ({{ fora_do_local|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="filtrarTabela('nao localizado', this)">
                            Faltantes ({{ nao_localizados|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="filtrarTabela('indeterminado', this)">
                            Sala Indeterminada ({{ indeterminados|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="filtrarTabela('sem cobertura', this)">
                            Sem Cobertura ({{ sem_cobertura|length }})
                        </button>
                        <button type="button" class="btn btn-sm btn-outline-secondary btn-filtro" onclick="rolarParaSobras()">
                            Tags Sobrando ({{ nao_cadastrados|length }})
                        </button>
                    </div>
                    <div class="col-lg-4 col-md-12">
                        <div class="input-group input-group-sm">
                            <span class="input-group-text bg-white border-end-0">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="text-muted">
                                    <circle cx="11" cy="11" r="8"></circle>
                                    <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
                                </svg>
                            </span>
                            <input type="text" id="inputBuscaRelatorio" class="form-control border-start-0 ps-0" placeholder="Filtrar por patrimônio, descrição, sala...">
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- Tabela Principal: Relação Analítica de Bens -->
        <div class="card mb-4">
            <div class="card-header py-3 d-flex justify-content-between align-items-center">
                <h6 class="mb-0 fw-bold">Relação Patrimonial Conferida</h6>
                <span class="chip-contador">{{ relatorio.bens|length }} bens auditados</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive">
                    <table class="table table-hover align-middle mb-0 tabela-cadastro" id="tabelaConferencia">
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
                                    <span class="tag-mono">{{ b.epc }}</span>
                                    {% else %}
                                    <span class="text-muted fst-italic">Sem tag</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if b.sala_esperada %}<span class="chip-sala">{{ b.sala_esperada }}</span>{% else %}<span class="chip-alerta">Sem sala</span>{% endif %}
                                </td>
                                <td>
                                    {% if b.salas_lidas %}
                                        {% for sl in b.salas_lidas %}
                                        <span class="badge {% if sl == b.sala_esperada %}badge-localizado{% else %}badge-fora-local{% endif %}"{% if sl in b.sinal_por_sala %} title="RSSI da melhor varredura nesta sala"{% endif %}>
                                            {{ sl }}{% if sl in b.sinal_por_sala %} · {{ "%g"|format(b.sinal_por_sala[sl]) }}{% endif %}
                                        </span>
                                        {% endfor %}
                                        {% if b.salas_lidas|length > 1 and b.sala_provavel %}
                                        <div class="small text-muted mt-1">Provável: <strong>{{ b.sala_provavel }}</strong></div>
                                        {% endif %}
                                    {% else %}
                                        <span class="text-muted fst-italic small">Não detectado</span>
                                    {% endif %}
                                </td>
                                <td>
                                    {% if b.situacao == 'localizado' %}
                                        <span class="badge badge-localizado">Localizado</span>
                                    {% elif b.situacao == 'fora do local' %}
                                        <span class="badge badge-fora-local">Fora do Local</span>
                                    {% elif b.situacao == 'nao localizado' %}
                                        <span class="badge badge-nao-localizado">Não Localizado</span>
                                    {% elif b.situacao == 'sem cobertura' %}
                                        <span class="badge badge-sem-cobertura">Sem Cobertura</span>
                                    {% elif b.situacao == 'indeterminado' %}
                                        <span class="badge badge-indeterminado">Sala Indeterminada</span>
                                    {% else %}
                                        <span class="badge bg-light text-dark border">{{ b.situacao }}</span>
                                    {% endif %}
                                </td>
                                <td class="small">
                                    {% if b.situacao == 'localizado' %}
                                        {% if b.criterio == 'confirmado' %}
                                        <span class="text-success fw-semibold">Confirmado em campo na sala esperada.</span>
                                        {% elif b.criterio == 'sinal' %}
                                        <span class="text-success fw-semibold">Item na sala esperada:</span> o sinal foi mais forte nela.
                                        {% elif b.criterio == 'cadastro' %}
                                        <span class="text-success fw-semibold">Item na sala esperada</span> <span class="text-muted">(lido também em outra sala com sinal parecido; vale o cadastro)</span>
                                        {% else %}
                                        <span class="text-success fw-semibold">Item confirmado no local correto.</span>
                                        {% endif %}
                                    {% elif b.situacao == 'fora do local' %}
                                        {% if b.criterio == 'confirmado' %}
                                        <span class="text-warning-emphasis fw-bold">Confirmado em campo em {{ b.sala_provavel }}:</span> esperado em {{ b.sala_esperada }}.
                                        {% elif b.criterio == 'sinal' %}
                                        <span class="text-warning-emphasis fw-bold">Transferência provável para {{ b.sala_provavel }}:</span> esperado em {{ b.sala_esperada }}, com sinal mais forte em {{ b.sala_provavel }}.
                                        {% else %}
                                        <span class="text-warning-emphasis fw-bold">Transferência não autorizada:</span> esperado em {{ b.sala_esperada }}, mas lido em {{ b.salas_lidas|join(', ') }}.
                                        {% endif %}
                                    {% elif b.situacao == 'nao localizado' %}
                                        <span class="text-danger fw-semibold">Faltante na sala {{ b.sala_esperada }}.</span> Verificar extravio ou leitura manual.
                                    {% elif b.situacao == 'sem cobertura' %}
                                        <span class="text-muted">A sala {{ b.sala_esperada }} ainda não foi varrida nesta sessão.</span>
                                    {% elif b.situacao == 'indeterminado' %}
                                        {% if b.criterio == 'sala nao varrida' %}
                                        <span class="text-info-emphasis fw-bold">Lido em {{ b.salas_lidas|join(', ') }}, mas a sala {{ b.sala_esperada }} não foi varrida.</span> Varra a sala {{ b.sala_esperada }} para decidir.
                                        {% else %}
                                        <span class="text-info-emphasis fw-bold">Lido em {{ b.salas_lidas|join(', ') }} com sinal parecido.</span> Procure o item no local.
                                        {% endif %}
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
            <div class="card-header py-3 d-flex justify-content-between align-items-center">
                <div class="d-flex align-items-center gap-2">
                    <span class="status-dot" style="background-color: var(--ciano);"></span>
                    <div>
                        <h6 class="mb-0 fw-bold text-info-emphasis">Tags RFID Sobrando no Ambiente (Não Cadastradas)</h6>
                        <small class="text-muted">Etiquetas lidas pelo coletor que não possuem correspondência patrimonial no cadastro</small>
                    </div>
                </div>
                <span class="badge badge-sobras">{{ relatorio.epcs_nao_cadastrados|length }} {% if relatorio.epcs_nao_cadastrados|length == 1 %}tag{% else %}tags{% endif %}</span>
            </div>
            <div class="card-body p-0">
                <div class="table-responsive" id="containerTabelaSobras" style="max-height: 380px; overflow-y: auto;">
                    <table class="table table-hover align-middle mb-0 tabela-cadastro">
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
                                    <span class="tag-mono">{{ item.epc }}</span>
                                </td>
                                <td>
                                    {% for s in item.salas_lidas %}
                                    <span class="chip-neutro">{{ s }}{% if s in item.sinal_por_sala %} · {{ "%g"|format(item.sinal_por_sala[s]) }}{% endif %}</span>
                                    {% endfor %}
                                    {% if item.salas_lidas|length > 1 and item.sala_provavel %}
                                    <div class="small text-muted mt-1">Provável: <strong>{{ item.sala_provavel }}</strong></div>
                                    {% endif %}
                                </td>
                                <td>
                                    <span class="badge badge-sobras">Não Cadastrado</span>
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
            <div class="mb-3 text-muted">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="text-secondary opacity-50">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                    <polyline points="14 2 14 8 20 8"></polyline>
                    <line x1="16" y1="13" x2="8" y2="13"></line>
                    <line x1="16" y1="17" x2="8" y2="17"></line>
                    <polyline points="10 9 9 9 8 9"></polyline>
                </svg>
            </div>
            <h5 class="fw-bold">Nenhuma sessão de inventário disponível para visualização</h5>
            <p class="text-muted small">
                Para visualizar a conferência, realize uma coleta com o Urovo DT50 ou utilize o quadro acima para importar os arquivos CSV offline.
            </p>
        </div>
        {% endif %}
    </div>

    <footer class="rodape no-print">
        <div class="container-fluid px-4 pagina-container py-3 d-flex justify-content-between align-items-center flex-wrap gap-2 small text-muted">
            <span><strong class="text-secondary">FIEA Inventário RFID</strong> · Servidor local nesta máquina</span>
            <span>Dados armazenados em <code>dados/inventario.db</code></span>
        </div>
    </footer>

    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script>
        // Toggle da área de importação
        function copiarIpServidor() {
            const texto = 'http://{{ ip_local }}:5000';
            navigator.clipboard.writeText(texto).then(() => {
                const label = document.getElementById('labelCopiarIpRelatorio');
                if (label) {
                    const original = label.innerText;
                    label.innerText = 'Copiado!';
                    setTimeout(() => { label.innerText = original; }, 2000);
                }
            }).catch(() => {
                prompt('Copie o endereço do servidor:', texto);
            });
        }

        function toggleAreaImportacao() {
            const alvo = document.getElementById('corpoImportacao');
            if (!alvo || !window.bootstrap) return;
            const vaiAbrir = !alvo.classList.contains('show');
            bootstrap.Collapse.getOrCreateInstance(alvo).toggle();
            if (vaiAbrir) {
                document.getElementById('cardImportacao').scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }

        // Chevron e rótulo dos cabeçalhos expansíveis
        function configurarToggle(alvoId, textoClasse, iconeClasse) {
            const alvo = document.getElementById(alvoId);
            if (!alvo) return;
            alvo.addEventListener('show.bs.collapse', () => {
                const t = document.querySelector('.' + textoClasse); if (t) t.textContent = 'Recolher';
                const i = document.querySelector('.' + iconeClasse); if (i) i.style.transform = 'rotate(180deg)';
            });
            alvo.addEventListener('hide.bs.collapse', () => {
                const t = document.querySelector('.' + textoClasse); if (t) t.textContent = 'Expandir';
                const i = document.querySelector('.' + iconeClasse); if (i) i.style.transform = 'rotate(0deg)';
            });
        }
        configurarToggle('corpoImportacao', 'texto-toggle-importacao', 'icone-chevron-importacao');
        configurarToggle('corpoSessaoAtual', 'texto-toggle-sessao', 'icone-chevron-sessao');

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
                let html = '<div class="alert alert-info py-2 mb-0"><h6 class="mb-1 fw-bold">' + files.length + ' arquivo(s) CSV selecionado(s):</h6><ul class="mb-0 ps-3">';
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
        // Monitoramento e alternância de máscara (Token e Endereço Completo)
        let tokenVisivel = false;
        let enderecoVisivel = false;

        function alternarMascaraToken() {
            tokenVisivel = !tokenVisivel;
            document.querySelectorAll('.campo-token-mascaravel').forEach(el => {
                el.innerText = tokenVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-token').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = tokenVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = tokenVisivel ? 'inline-block' : 'none';
                }
            });
        }

        function alternarMascaraEndereco() {
            enderecoVisivel = !enderecoVisivel;
            document.querySelectorAll('.campo-endereco-mascaravel').forEach(el => {
                el.innerText = enderecoVisivel ? el.getAttribute('data-real') : el.getAttribute('data-mascarado');
            });
            document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
                const olhoAberto = btn.querySelector('.icone-olho-aberto');
                const olhoFechado = btn.querySelector('.icone-olho-fechado');
                if (olhoAberto && olhoFechado) {
                    olhoAberto.style.display = enderecoVisivel ? 'none' : 'inline-block';
                    olhoFechado.style.display = enderecoVisivel ? 'inline-block' : 'none';
                }
            });
        }

        document.querySelectorAll('.btn-toggle-token').forEach(btn => {
            btn.addEventListener('click', alternarMascaraToken);
        });
        document.querySelectorAll('.btn-toggle-endereco').forEach(btn => {
            btn.addEventListener('click', alternarMascaraEndereco);
        });

        // Ao fechar qualquer modal de conexão/ajuda, reoculta o endereço completo por privacidade
        document.querySelectorAll('.modal-conexao-ajuda').forEach(modalEl => {
            modalEl.addEventListener('hidden.bs.modal', () => {
                if (enderecoVisivel) {
                    alternarMascaraEndereco();
                }
            });
        });

        function atualizarStatusColetor() {
            fetch('/api/status-coletor')
                .then(r => r.json())
                .then(data => {
                    const badge = document.getElementById('badgeColetor');
                    const dot = document.getElementById('dotColetor');
                    const texto = document.getElementById('textoColetor');
                    if (!badge) return;

                    if (data && data.conectado) {
                        if (data.fila_pendente && data.fila_pendente > 0) {
                            badge.className = 'chip chip-warn';
                            dot.className = 'status-dot warning';
                            texto.innerText = `Coletor Conectado · ${data.fila_pendente} pendência(s) na fila`;
                        } else {
                            badge.className = 'chip chip-ok';
                            dot.className = 'status-dot online';
                            const tempo = (data.segundos_atras !== null && data.segundos_atras !== undefined) ? `há ${data.segundos_atras}s` : 'ativo';
                            texto.innerText = `Coletor Conectado`;
                        }
                    } else {
                        badge.className = 'chip chip-off';
                        dot.className = 'status-dot offline';
                        texto.innerText = 'Coletor: Aguardando Wi-Fi';
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


@app.route("/favicon.ico")
def favicon():
    resp = make_response(FAVICON_ICO_BYTES)
    resp.headers["Content-Type"] = "image/x-icon"
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.route("/favicon.png")
def favicon_png():
    resp = make_response(FAVICON_PNG_BYTES)
    resp.headers["Content-Type"] = "image/png"
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


@app.route("/js/qrcode.min.js")
def rota_qrcode_js():
    """Entrega a biblioteca leve de geração de QR Code localmente."""
    caminho = Path(__file__).parent / "static" / "qrcode.min.js"
    if caminho.exists():
        resp = make_response(caminho.read_text(encoding="utf-8"))
        resp.headers["Content-Type"] = "application/javascript; charset=utf-8"
        return resp
    return make_response("", 404)





@app.route("/")
def index():
    """Painel Inicial / Dashboard de Boas-Vindas e Conexão com o Urovo."""
    con = get_db()
    total_bens = con.execute("SELECT COUNT(*) FROM bem").fetchone()[0]
    total_salas = con.execute("SELECT COUNT(*) FROM sala").fetchone()[0]
    total_sessoes = con.execute("SELECT COUNT(*) FROM sessao").fetchone()[0]
    ip_local = obter_ip_local()
    token_conexao = gerar_token_conexao(ip_local)
    return render_template_string(
        HTML_HOME_TEMPLATE,
        total_bens=total_bens,
        total_salas=total_salas,
        total_sessoes=total_sessoes,
        ip_local=ip_local,
        token_conexao=token_conexao,
    )


@app.route("/bens")
def gerenciar_bens():
    """Gestão da Relação Patrimonial de Bens e Vínculo de Tags RFID."""
    con = get_db()
    bens = con.execute(
        "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, b.sala_esperada_id, s.nome AS sala "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "ORDER BY s.nome, b.patrimonio"
    ).fetchall()
    salas = con.execute("SELECT id, nome, unidade FROM sala ORDER BY nome").fetchall()
    ip_local = obter_ip_local()
    token_conexao = gerar_token_conexao(ip_local)
    msg = request.args.get("msg")
    tipo_msg = request.args.get("tipo", "info")

    etiqueta_pat = request.args.get("etiqueta_pat")
    bem_etiqueta = None
    if etiqueta_pat:
        bem_etiqueta = con.execute(
            "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, s.nome AS sala, s.unidade "
            "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
            "WHERE b.patrimonio = ?",
            (etiqueta_pat,),
        ).fetchone()

    conflito = None
    if request.args.get("conflito_epc"):
        conflito = {
            "epc": request.args.get("conflito_epc"),
            "dono_pat": request.args.get("conflito_dono_pat"),
            "dono_desc": request.args.get("conflito_dono_desc"),
            "dono_sala": request.args.get("conflito_dono_sala"),
            "novo_pat": request.args.get("novo_pat"),
            "novo_desc": request.args.get("novo_desc"),
            "novo_sala": request.args.get("novo_sala"),
            "novo_metal": "1" if request.args.get("novo_metal") in ("1", "on", "true") else "0",
            "editando": "1" if request.args.get("conflito_editando") == "1" else "0",
        }

    return render_template_string(
        HTML_TEMPLATE,
        bens=bens,
        salas=salas,
        ip_local=ip_local,
        token_conexao=token_conexao,
        mensagem=msg,
        tipo_msg=tipo_msg,
        conflito=conflito,
        bem_etiqueta=bem_etiqueta,
    )


@app.route("/cadastrar", methods=["POST"])
def cadastrar_bem():
    patrimonio = request.form.get("patrimonio", "").strip()
    descricao = request.form.get("descricao", "").strip()
    epc = request.form.get("epc", "").strip().upper() or None
    sala_escolhida = request.form.get("sala", "").strip()
    metalico = 1 if str(request.form.get("metalico", "")).strip().lower() in ("1", "on", "true") else 0
    confirmar_transferencia = request.form.get("confirmar_transferencia") == "1"
    editando = request.form.get("editando") == "1"

    if not patrimonio or not descricao or not sala_escolhida:
        return redirect(url_for("gerenciar_bens", msg="Patrimônio, descrição e sala são obrigatórios.", tipo="danger"))

    con = get_db()
    # Verifica se o patrimônio já existe no banco
    bem_existente = con.execute(
        "SELECT b.patrimonio, b.descricao, b.epc, b.metalico, s.nome AS sala "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "WHERE b.patrimonio = ?",
        (patrimonio,),
    ).fetchone()

    # Se for um cadastro novo (não editando), bloqueia duplicação de patrimônio
    if not editando and bem_existente:
        sala_nome = bem_existente["sala"] or "Sem sala"
        msg_erro = (
            f"O patrimônio '{patrimonio}' já está cadastrado ({bem_existente['descricao']} na sala {sala_nome}). "
            f"Cada patrimônio deve ser único. Para editar este bem, utilize o botão 'Editar' na tabela."
        )
        return redirect(url_for("gerenciar_bens", msg=msg_erro, tipo="danger"))

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
        return redirect(url_for("gerenciar_bens", msg=f"A sala '{sala_escolhida}' não foi encontrada.", tipo="danger"))

    sala_id = sala["id"]

    msg_sucesso_extra = ""
    # Verifica se a tag EPC já está cadastrada em outro bem
    if epc:
        dono = con.execute(
            "SELECT b.patrimonio, b.descricao, s.nome AS sala "
            "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
            "WHERE b.epc = ? AND b.patrimonio != ?",
            (epc, patrimonio),
        ).fetchone()

        if dono is not None:
            if not confirmar_transferencia:
                sala_nome = dono["sala"] or "Sem sala"
                msg_conflito = (
                    f"A tag EPC {epc} já está vinculada ao bem {dono['patrimonio']} "
                    f"({dono['descricao']}) na sala {sala_nome}."
                )
                return redirect(url_for(
                    "gerenciar_bens",
                    msg=msg_conflito,
                    tipo="warning",
                    conflito_epc=epc,
                    conflito_dono_pat=dono["patrimonio"],
                    conflito_dono_desc=dono["descricao"] or "",
                    conflito_dono_sala=sala_nome,
                    novo_pat=patrimonio,
                    novo_desc=descricao,
                    novo_sala=str(sala_id),
                    novo_metal=str(metalico),
                    conflito_editando="1" if editando else "0",
                ))
            else:
                con.execute("UPDATE bem SET epc = NULL WHERE epc = ?", (epc,))
                msg_sucesso_extra = f" Tag {epc} transferida do bem {dono['patrimonio']}."

    # Insere novo bem ou atualiza bem existente
    try:
        if editando:
            con.execute(
                "UPDATE bem SET descricao = ?, sala_esperada_id = ?, epc = ?, metalico = ? "
                "WHERE patrimonio = ?",
                (descricao, sala_id, epc, metalico, patrimonio),
            )
        else:
            con.execute(
                "INSERT INTO bem (patrimonio, descricao, sala_esperada_id, epc, metalico, importado_em) "
                "VALUES (?, ?, ?, ?, ?, ?)",
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
        return redirect(url_for("gerenciar_bens", msg=f"Erro ao salvar: {e}", tipo="danger"))

    msg_final = f"Bem {patrimonio} salvo com sucesso!{msg_sucesso_extra}"
    return redirect(url_for("gerenciar_bens", msg=msg_final, tipo="success", etiqueta_pat=patrimonio))


@app.route("/excluir/<patrimonio>", methods=["POST"])
def excluir_bem(patrimonio):
    con = get_db()
    con.execute("DELETE FROM bem WHERE patrimonio = ?", (patrimonio,))
    con.commit()
    return redirect(url_for("gerenciar_bens", msg=f"Bem {patrimonio} excluído.", tipo="warning"))


@app.route("/salas")
def listar_salas():
    """Página dedicada à gestão e cadastro de salas."""
    con = get_db()
    salas = con.execute(
        "SELECT s.id, s.nome, s.unidade, "
        "       COUNT(DISTINCT b.patrimonio) AS total_bens, "
        "       COUNT(DISTINCT v.id) AS total_varreduras "
        "FROM sala s "
        "LEFT JOIN bem b ON b.sala_esperada_id = s.id "
        "LEFT JOIN varredura v ON v.sala_id = s.id "
        "GROUP BY s.id, s.nome, s.unidade "
        "ORDER BY s.nome"
    ).fetchall()
    ip_local = obter_ip_local()
    token_conexao = gerar_token_conexao(ip_local)
    msg = request.args.get("msg")
    tipo_msg = request.args.get("tipo", "info")
    return render_template_string(
        HTML_SALAS_TEMPLATE, salas=salas, ip_local=ip_local, token_conexao=token_conexao, mensagem=msg, tipo_msg=tipo_msg
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
    """Exclui uma sala caso não haja bens ou varreduras associados."""
    con = get_db()
    sala = con.execute("SELECT id, nome FROM sala WHERE id = ?", (sala_id,)).fetchone()
    if not sala:
        return redirect(url_for("listar_salas", msg="Sala não encontrada.", tipo="warning"))
    nome_sala = sala["nome"]

    # 1. Verifica se existem bens vinculados a esta sala
    row_bem = con.execute("SELECT COUNT(*) FROM bem WHERE sala_esperada_id = ?", (sala_id,)).fetchone()
    bens_vinculados = row_bem[0] if row_bem else 0
    if bens_vinculados > 0:
        texto = "1 bem vinculado" if bens_vinculados == 1 else f"{bens_vinculados} bens vinculados"
        return redirect(url_for("listar_salas", msg=f"Não é possível excluir: existe {texto} à sala '{nome_sala}'.", tipo="danger"))

    # 2. Verifica se existem varreduras de inventário registradas nesta sala
    row_var = con.execute("SELECT COUNT(*) FROM varredura WHERE sala_id = ?", (sala_id,)).fetchone()
    varreduras_vinculadas = row_var[0] if row_var else 0
    if varreduras_vinculadas > 0:
        texto = "1 varredura registrada" if varreduras_vinculadas == 1 else f"{varreduras_vinculadas} varreduras registradas"
        return redirect(url_for("listar_salas", msg=f"Não é possível excluir: existe {texto} nesta sala no histórico de auditoria.", tipo="danger"))

    # 3. Tenta a exclusão com tratamento de integridade referencial
    try:
        con.execute("DELETE FROM sala WHERE id = ?", (sala_id,))
        con.commit()
    except sqlite3.IntegrityError:
        return redirect(url_for("listar_salas", msg=f"Não é possível excluir a sala '{nome_sala}': ela possui vínculos no sistema.", tipo="danger"))

    return redirect(url_for("listar_salas", msg=f"Sala '{nome_sala}' excluída com sucesso.", tipo="warning"))


def formatar_data_hora(valor):
    """Formata um instante textual (ex.: '2026-10-21 16:10:20.759') como 'dd/mm/aaaa hh:mm'.

    Tolerante a variações de formato; se não reconhecer, devolve o texto original.
    Trata timestamps nulos ou ano <= 1970 (sessão em andamento) como 'Em aberto'.
    """
    if not valor:
        return "Em aberto"
    texto = str(valor).strip()
    if texto in ("1969-12-31 21:00:00", "1970-01-01 00:00:00") or texto.startswith("1969-") or texto.startswith("1970-"):
        return "Em aberto"
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(texto, fmt)
            if dt.year <= 1970:
                return "Em aberto"
            return dt.strftime("%d/%m/%Y %H:%M")
        except ValueError:
            continue
    return texto


def formatar_duracao(duracao_s):
    """Formata duração em segundos para texto legível: '45s', '12min 30s', '1h 15min 20s'."""
    if duracao_s is None:
        return ""
    try:
        total = int(duracao_s)
    except (ValueError, TypeError):
        return ""
    if total <= 0:
        return "0s"
    if total < 60:
        return f"{total}s"
    if total < 3600:
        mins = total // 60
        segs = total % 60
        return f"{mins}min {segs}s" if segs > 0 else f"{mins}min"
    horas = total // 3600
    resto = total % 3600
    mins = resto // 60
    segs = resto % 60
    partes = [f"{horas}h"]
    if mins > 0:
        partes.append(f"{mins}min")
    if segs > 0:
        partes.append(f"{segs}s")
    return " ".join(partes)


app.jinja_env.globals["formatar_data_hora"] = formatar_data_hora
app.jinja_env.globals["formatar_duracao"] = formatar_duracao


@app.route("/relatorio")
def ver_relatorio():
    """Visualização da conferência e auditoria patrimonial (RF07 / RF08)."""
    con = get_db()
    sessoes = con.execute(
        "SELECT s.id, s.unidade, s.operador, s.inicio, s.fim, s.duracao_s, s.n_varreduras, s.encerrada_auto, "
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
    indeterminados = []
    nao_cadastrados = []
    pct_localizados = 0.0

    if sessao_id:
        sessao_atual = con.execute("SELECT * FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
        varreduras = con.execute(
            "SELECT v.numero, v.inicio, v.fim, v.duracao_s, v.linhas_brutas, s.nome AS sala, "
            # Etiquetas lidas pelo rádio; as resoluções de Pendências não são leituras.
            "       COUNT(CASE WHEN l.metodo = 'RFID' THEN 1 END) AS total_etiquetas "
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
                elif b.situacao == conferencia.INDETERMINADO:
                    indeterminados.append(b)

            nao_cadastrados = relatorio.epcs_nao_cadastrados
            if relatorio.bens:
                pct_localizados = round((len(localizados) / len(relatorio.bens)) * 100, 1)
        except conferencia.SessaoNaoEncontrada:
            relatorio = None

    sessao_inicio_fmt = formatar_data_hora(sessao_atual["inicio"]) if sessao_atual else "Em aberto"
    sessao_fim_fmt = formatar_data_hora(sessao_atual["fim"]) if sessao_atual else "Em aberto"

    ip_local = obter_ip_local()
    msg = request.args.get("msg")
    tipo_msg = request.args.get("tipo", "info")

    return render_template_string(
        HTML_RELATORIO_TEMPLATE,
        sessoes=sessoes,
        sessao_atual=sessao_atual,
        sessao_inicio_fmt=sessao_inicio_fmt,
        sessao_fim_fmt=sessao_fim_fmt,
        sessao_id=sessao_id,
        varreduras=varreduras,
        relatorio=relatorio,
        localizados=localizados,
        fora_do_local=fora_do_local,
        nao_localizados=nao_localizados,
        sem_cobertura=sem_cobertura,
        indeterminados=indeterminados,
        nao_cadastrados=nao_cadastrados,
        pct_localizados=pct_localizados,
        ip_local=ip_local,
        token_conexao=gerar_token_conexao(ip_local),
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

    # Em ordem de nome: leituras_* entra antes de pendencias_* (que precisa da varredura).
    for f in sorted(arquivos, key=lambda a: a.filename or ""):
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
            elif resultado.tipo in ("varredura", "pendencia") and not sessao_id_detectada:
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


# Critério da sala provável (conferencia.CRITERIO_*), como sai no CSV exportado.
ROTULOS_CRITERIO = {
    conferencia.CRITERIO_CONFIRMADO: "Confirmado em campo (QR ou manual)",
    conferencia.CRITERIO_UNICA: "Lido em uma sala só",
    conferencia.CRITERIO_SINAL: "Sinal mais forte",
    conferencia.CRITERIO_CADASTRO: "Sinal parecido; vale o cadastro",
    conferencia.CRITERIO_EMPATE: "Sinal parecido entre as salas",
    conferencia.CRITERIO_SALA_NAO_VARRIDA: "Sala esperada não varrida",
}


def _texto_sinais(sinal_por_sala):
    """RSSI de cada sala (ex.: Lab A: 82; Lab B: 71), para conferir a decisão na planilha."""
    return "; ".join(f"{sala}: {valor:g}" for sala, valor in sinal_por_sala.items())


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
    # As colunas da sala provável vão no fim, para não deslocar as que planilhas já leem.
    writer.writerow([
        "Patrimonio", "Descricao", "EPC", "Sala_Esperada", "Salas_Lidas",
        "Situacao", "Unidade", "Sessao_ID", "Diagnostico",
        "Sala_Provavel", "Criterio", "Sinal_Por_Sala",
    ])

    for b in rel.bens:
        salas_lidas_str = ", ".join(b.salas_lidas) if b.salas_lidas else "Nenhuma"
        if b.situacao == conferencia.LOCALIZADO:
            obs = "Item confirmado na sala correta"
            if any(s != b.sala_esperada for s in b.salas_lidas):
                obs += f" (também detectado em outra sala: {salas_lidas_str})"
        elif b.situacao == conferencia.FORA_DO_LOCAL:
            if b.criterio in (conferencia.CRITERIO_SINAL, conferencia.CRITERIO_CONFIRMADO):
                obs = (f"Transferência provável: esperado em '{b.sala_esperada}', "
                       f"provável em '{b.sala_provavel}' (lido em '{salas_lidas_str}')")
            else:
                obs = f"Transferência indevida: esperado em '{b.sala_esperada}', mas detectado em '{salas_lidas_str}'"
        elif b.situacao == conferencia.NAO_LOCALIZADO:
            obs = f"Faltante: não detectado na sala '{b.sala_esperada}' durante a varredura"
        elif b.situacao == conferencia.SEM_COBERTURA:
            obs = f"Sem cobertura: a sala '{b.sala_esperada}' não foi varrida nesta sessão"
        elif b.situacao == conferencia.INDETERMINADO:
            if b.criterio == conferencia.CRITERIO_SALA_NAO_VARRIDA:
                obs = (f"Sala indeterminada: lido em '{salas_lidas_str}', mas a sala "
                       f"'{b.sala_esperada}' não foi varrida nesta sessão")
            else:
                obs = f"Sala indeterminada: lido em '{salas_lidas_str}' com sinal parecido; procurar o item"
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
            b.sala_provavel,
            ROTULOS_CRITERIO.get(b.criterio, b.criterio),
            _texto_sinais(b.sinal_por_sala),
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
            item.sala_provavel,
            ROTULOS_CRITERIO.get(item.criterio, item.criterio),
            _texto_sinais(item.sinal_por_sala),
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
    fila = request.args.get("fila")
    registrar_contato_coletor("health-check", fila=fila)
    ip = obter_ip_local()
    return jsonify({
        "status": "online",
        "servidor": "FIEA Coleta Local",
        "ip": ip,
        "token": gerar_token_conexao(ip),
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/api/status-coletor", methods=["GET"])
def api_status_coletor():
    """Consulta para o dashboard web saber se o Urovo está conectado."""
    ts = ultimo_contato_coletor.get("timestamp")
    conectado = bool(ts and (time.time() - ts < 45))
    segundos = int(time.time() - ts) if ts else None
    fila = ultimo_contato_coletor.get("fila_pendente", 0)
    ip = obter_ip_local()
    return jsonify({
        "conectado": conectado,
        "ip": ultimo_contato_coletor.get("ip"),
        "acao": ultimo_contato_coletor.get("acao"),
        "segundos_atras": segundos,
        "fila_pendente": fila,
        "token": gerar_token_conexao(ip),
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


@app.route("/api/verificar-patrimonio", methods=["GET"])
def api_verificar_patrimonio():
    """Consulta em tempo real se um patrimônio já está cadastrado no sistema."""
    patrimonio = request.args.get("patrimonio", "").strip()
    if not patrimonio:
        return jsonify({"status": "vazio"})

    con = get_db()
    bem = con.execute(
        "SELECT b.patrimonio, b.descricao, s.nome AS sala "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "WHERE b.patrimonio = ?",
        (patrimonio,),
    ).fetchone()

    if not bem:
        return jsonify({"status": "disponivel", "patrimonio": patrimonio})
    else:
        return jsonify({
            "status": "em_uso",
            "patrimonio": bem["patrimonio"],
            "descricao": bem["descricao"] or "",
            "sala": bem["sala"] or "Sem sala",
        })


@app.route("/api/verificar-epc", methods=["GET"])
def api_verificar_epc():
    """Consulta em tempo real se um EPC já está cadastrado em outro bem."""
    epc = request.args.get("epc", "").strip().upper()
    patrimonio_atual = request.args.get("patrimonio", "").strip()
    if not epc:
        return jsonify({"status": "vazio"})

    con = get_db()
    dono = con.execute(
        "SELECT b.patrimonio, b.descricao, s.nome AS sala "
        "FROM bem b LEFT JOIN sala s ON s.id = b.sala_esperada_id "
        "WHERE b.epc = ?",
        (epc,),
    ).fetchone()

    if not dono:
        return jsonify({"status": "disponivel", "epc": epc})
    elif dono["patrimonio"] == patrimonio_atual:
        return jsonify({
            "status": "proprio_bem",
            "epc": epc,
            "patrimonio": dono["patrimonio"],
            "descricao": dono["descricao"] or "",
        })
    else:
        return jsonify({
            "status": "em_uso",
            "epc": epc,
            "patrimonio": dono["patrimonio"],
            "descricao": dono["descricao"] or "",
            "sala": dono["sala"] or "Sem sala",
        })


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


def _numero(valor, padrao):
    try:
        return float(valor)
    except (TypeError, ValueError):
        return padrao


def agregar_leituras(leituras):
    """Uma linha por EPC e método, como o leitor_csv faz com o CSV da varredura.

    O coletor manda um evento por leitura ({"epc", "rssi", "primeira_leitura", ...}).
    Gravados um a um, cada evento sobrescrevia o anterior e sobravam 1 leitura e RSSI 0,
    justamente o que a conferência precisa para saber em que sala o sinal foi mais forte.
    Itens já agregados (com "leituras" e "rssi_min/medio/max") também são aceitos.
    """
    agregadas = {}
    for l in leituras:
        epc = (l.get("epc") or "").strip().upper()
        if not epc:
            continue
        metodo = (l.get("metodo") or "RFID").strip().upper()
        quantidade = max(1, int(_numero(l.get("leituras", 1), 1)))
        if any(chave in l for chave in ("rssi_min", "rssi_medio", "rssi_max")):
            rssi_min = _numero(l.get("rssi_min"), 0)
            rssi_max = _numero(l.get("rssi_max"), 0)
            rssi_medio = _numero(l.get("rssi_medio"), (rssi_min + rssi_max) / 2)
        else:
            rssi_min = rssi_max = rssi_medio = _numero(l.get("rssi"), 0)
        primeira = l.get("primeira_leitura") or datetime.now().strftime("%Y-%m-%d %H:%M:%S.000")
        ultima = l.get("ultima_leitura") or primeira

        atual = agregadas.get((epc, metodo))
        if atual is None:
            agregadas[(epc, metodo)] = {
                "epc": epc, "metodo": metodo, "leituras": quantidade,
                "soma": rssi_medio * quantidade, "rssi_min": rssi_min, "rssi_max": rssi_max,
                "primeira_leitura": primeira, "ultima_leitura": ultima,
            }
        else:
            atual["leituras"] += quantidade
            atual["soma"] += rssi_medio * quantidade
            atual["rssi_min"] = min(atual["rssi_min"], rssi_min)
            atual["rssi_max"] = max(atual["rssi_max"], rssi_max)
            # Mesmo formato de data do coletor, então a ordem do texto é a ordem do tempo.
            atual["primeira_leitura"] = min(atual["primeira_leitura"], primeira)
            atual["ultima_leitura"] = max(atual["ultima_leitura"], ultima)

    resultado = []
    for a in agregadas.values():
        resultado.append({
            "epc": a["epc"],
            "metodo": a["metodo"],
            "leituras": a["leituras"],
            "rssi_min": int(a["rssi_min"]),
            "rssi_medio": round(a["soma"] / a["leituras"], 2),
            "rssi_max": int(a["rssi_max"]),
            "primeira_leitura": a["primeira_leitura"],
            "ultima_leitura": a["ultima_leitura"],
        })
    return resultado


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
    if ultimo_contato_coletor.get("fila_pendente", 0) > 0:
        ultimo_contato_coletor["fila_pendente"] -= 1

    sessao_info = dados.get("sessao", {})
    varredura_info = dados.get("varredura", {})
    leituras = dados.get("leituras", [])
    pendencias = dados.get("pendencias", [])

    con = get_db()

    # 1. Sala da varredura (resolução prévia e estrita, antes de qualquer gravação):
    # nome desconhecido na unidade → 409 e nada é gravado no banco. Sem criação
    # silenciosa de salas por sincronização (a sala se cadastra na web).
    sala_nome = varredura_info.get("sala", "").strip()
    unidade = sessao_info.get("unidade", "UFAL")
    sala_id = None
    if sala_nome:
        s_row = con.execute(
            "SELECT id FROM sala WHERE LOWER(nome) = LOWER(?) AND unidade = ?",
            (sala_nome, unidade),
        ).fetchone()
        if s_row is None:
            salas = con.execute(
                "SELECT nome FROM sala WHERE unidade = ? ORDER BY nome",
                (unidade,),
            ).fetchall()
            return jsonify({
                "status": "sala_desconhecida",
                "sala": sala_nome,
                "unidade": unidade,
                "salas": [r["nome"] for r in salas],
            }), 409
        sala_id = s_row["id"]

    # 2. Atualiza/Cria Sessão
    if sessao_info.get("id"):
        fim_val = sessao_info.get("fim")
        duracao_val = sessao_info.get("duracao_s")

        # Sanitiza fim zerado ou ano <= 1970
        if fim_val:
            fim_str = str(fim_val).strip()
            if fim_str.startswith("1969-") or fim_str.startswith("1970-") or "1969" in fim_str or "1970" in fim_str:
                fim_val = None

        # Sanitiza duração negativa ou inválida
        if duracao_val is not None:
            try:
                duracao_int = int(duracao_val)
                duracao_val = duracao_int if duracao_int >= 0 else None
            except (ValueError, TypeError):
                duracao_val = None

        con.execute(
            "INSERT INTO sessao (id, unidade, operador, inicio, fim, duracao_s, n_varreduras) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "  unidade = excluded.unidade, operador = excluded.operador, "
            "  fim = CASE WHEN excluded.fim IS NOT NULL THEN excluded.fim ELSE sessao.fim END, "
            "  duracao_s = CASE WHEN excluded.duracao_s IS NOT NULL THEN excluded.duracao_s ELSE sessao.duracao_s END, "
            "  encerrada_auto = CASE WHEN excluded.fim IS NOT NULL THEN 0 ELSE sessao.encerrada_auto END, "
            "  n_varreduras = COALESCE(excluded.n_varreduras, sessao.n_varreduras)",
            (
                sessao_info["id"],
                sessao_info.get("unidade", "UFAL"),
                sessao_info.get("operador", "Operador"),
                sessao_info.get("inicio"),
                fim_val,
                duracao_val,
                sessao_info.get("n_varreduras", 1),
            ),
        )

    # 3. Cria/Atualiza Varredura
    varredura_id = varredura_info.get("id")
    if not varredura_id:
        if sessao_info.get("id"):
            con.commit()
            return jsonify({
                "status": "sucesso",
                "mensagem": "Sessão sincronizada com sucesso",
                "sessao_id": sessao_info.get("id"),
            })
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
    # Só as leituras do rádio são substituídas. As resoluções de Pendências ficam: um
    # envio antigo da fila offline, só com leituras, pode chegar depois do que levou as
    # resoluções, e antes as apagava.
    con.execute(
        "DELETE FROM leitura WHERE varredura_id = ? AND metodo = 'RFID'", (varredura_id,)
    )
    do_radio = [item for item in leituras if (item.get("metodo") or "RFID").upper() == "RFID"]
    for l in agregar_leituras(do_radio):
        con.execute(
            "INSERT INTO leitura (varredura_id, epc, metodo, leituras, rssi_min, "
            "rssi_medio, rssi_max, primeira_leitura, ultima_leitura) "
            "VALUES (?, ?, 'RFID', ?, ?, ?, ?, ?, ?)",
            (
                varredura_id,
                l["epc"],
                l["leituras"],
                l["rssi_min"],
                l["rssi_medio"],
                l["rssi_max"],
                l["primeira_leitura"],
                l["ultima_leitura"],
            ),
        )

    # 5. Grava Resolução de Pendências (QR Code, Manual ou Faltante - RF10)
    # O coletor manda a lista inteira de resoluções da varredura a cada envio; uma
    # lista nova substitui a anterior, e um envio sem resoluções não mexe nelas.
    importador.gravar_resolucoes(con, varredura_id, pendencias)

    con.commit()
    return jsonify({
        "status": "sucesso",
        "varredura_id": varredura_id,
        "leituras_gravadas": len(leituras),
        "pendencias_gravadas": len(pendencias),
    })


@app.route("/api/encerrar_sessao", methods=["POST"])
def encerrar_sessao_api():
    """Encerra uma sessão de inventário pelo leitor móvel."""
    dados = request.get_json(silent=True) or {}
    sessao_info = dados.get("sessao", dados)
    sessao_id = sessao_info.get("id") or sessao_info.get("sessao_id")
    if not sessao_id:
        return jsonify({"erro": "id da sessão é obrigatório"}), 400

    fim_val = sessao_info.get("fim")
    duracao_val = sessao_info.get("duracao_s")

    if not fim_val or "1969" in str(fim_val) or "1970" in str(fim_val):
        fim_val = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    con = get_db()
    row = con.execute("SELECT inicio FROM sessao WHERE id = ?", (sessao_id,)).fetchone()
    if row and row["inicio"]:
        try:
            if duracao_val is None or int(duracao_val) <= 0:
                inicio_str = row["inicio"]
                for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                    try:
                        dt_ini = datetime.strptime(inicio_str, fmt)
                        dt_fim = datetime.strptime(str(fim_val)[:19], "%Y-%m-%d %H:%M:%S")
                        duracao_val = max(0, int((dt_fim - dt_ini).total_seconds()))
                        break
                    except ValueError:
                        pass
        except Exception:
            pass

    con.execute(
        "UPDATE sessao SET fim = ?, duracao_s = ?, encerrada_auto = 0 WHERE id = ?",
        (fim_val, duracao_val, sessao_id),
    )
    con.commit()
    return jsonify({
        "status": "sucesso",
        "mensagem": "Sessão encerrada com sucesso",
        "sessao_id": sessao_id,
        "fim": fim_val,
        "duracao_s": duracao_val,
    })


@app.route("/api/salas", methods=["POST"])
def api_cadastrar_sala():
    """Cria uma sala no PC (usado pelo app quando a coletora aponta uma sala desconhecida)."""
    dados = request.get_json(force=True, silent=True) or {}
    nome = (dados.get("nome") or "").strip()
    unidade = (dados.get("unidade") or "UFAL").strip() or "UFAL"
    if not nome:
        return jsonify({"erro": "nome é obrigatório"}), 400
    con = get_db()
    existente = con.execute(
        "SELECT id, nome, unidade FROM sala WHERE unidade = ? AND LOWER(nome) = LOWER(?)",
        (unidade, nome),
    ).fetchone()
    if existente:
        return jsonify({
            "status": "ja_existia",
            "sala": {"id": existente["id"], "nome": existente["nome"], "unidade": existente["unidade"]},
        }), 200
    cur = con.execute(
        "INSERT INTO sala (unidade, nome) VALUES (?, ?)",
        (unidade, nome),
    )
    con.commit()
    return jsonify({
        "status": "sucesso",
        "sala": {"id": cur.lastrowid, "nome": nome, "unidade": unidade},
    }), 201


def iniciar_servidor(porta=5000):
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ip = obter_ip_local()
    print("=" * 65)
    print("SERVIDOR LOCAL DE INVENTARIO INICIADO COM SUCESSO!")
    print(f"Interface Web no PC: http://localhost:{porta}")
    print(f"Conexao Wi-Fi (Urovo DT50): http://{ip}:{porta}")
    print("=" * 65)
    app.run(host="0.0.0.0", port=porta, debug=False)


if __name__ == "__main__":
    iniciar_servidor()