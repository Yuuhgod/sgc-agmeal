"""Garante que a interface funciona sem internet: nenhuma dependência de CDN."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / 'app'
URL_EXTERNA = re.compile(r'(?:src|href)\s*=\s*["\']\s*(?:https?:)?//', re.IGNORECASE)

ARQUIVOS_VENDOR = [
    'vendor/bootstrap-5.3.2/css/bootstrap.min.css',
    'vendor/bootstrap-5.3.2/js/bootstrap.bundle.min.js',
    'vendor/fontawesome-6.4.2/css/all.min.css',
    'vendor/fontawesome-6.4.2/webfonts/fa-solid-900.woff2',
    'vendor/cropperjs-1.6.1/cropper.min.css',
    'vendor/cropperjs-1.6.1/cropper.min.js',
]


@pytest.mark.parametrize('template', sorted(p.name for p in (APP_DIR / 'templates').glob('*.html')))
def test_template_sem_recursos_externos(template):
    conteudo = (APP_DIR / 'templates' / template).read_text(encoding='utf-8')
    assert not URL_EXTERNA.search(conteudo), f'{template} carrega recurso de fora do servidor'


def test_csp_nao_libera_cdns(client):
    csp = client.get('/login').headers['Content-Security-Policy']
    assert 'cdn.jsdelivr.net' not in csp
    assert 'cdnjs.cloudflare.com' not in csp


@pytest.mark.parametrize('caminho', ARQUIVOS_VENDOR)
def test_vendor_servido_com_cache_longo(client, caminho):
    r = client.get(f'/static/{caminho}')
    assert r.status_code == 200
    assert len(r.data) > 1000
    assert 'immutable' in r.headers['Cache-Control']


def test_fontes_referenciadas_pelo_fontawesome_existem():
    css = (APP_DIR / 'static/vendor/fontawesome-6.4.2/css/all.min.css').read_text(encoding='utf-8')
    fontes = set(re.findall(r'url\(\.\./webfonts/([^)]+)\)', css))
    assert fontes
    faltando = [f for f in fontes if not (APP_DIR / 'static/vendor/fontawesome-6.4.2/webfonts' / f).is_file()]
    assert not faltando


def test_paginas_continuam_sem_cache(admin_client):
    assert 'no-store' in admin_client.get('/').headers['Cache-Control']
