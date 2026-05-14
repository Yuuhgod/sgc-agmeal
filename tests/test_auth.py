"""Testes de autenticação e proteção de rotas."""

from __future__ import annotations


def test_get_setup_redirects_when_users_exist(client, admin_credentials):
    r = client.get('/setup')
    assert r.status_code == 302


def test_get_login_ok(client):
    r = client.get('/login')
    assert r.status_code == 200


def test_post_login_wrong_password(client, admin_credentials):
    r = client.post(
        '/login',
        data={'username': 'admin', 'senha': 'senha_totalmente_errada'},
    )
    assert r.status_code == 200


def test_post_login_success_redirects(client, admin_credentials):
    r = client.post('/login', data=admin_credentials)
    assert r.status_code == 302
    assert '/login' not in (r.headers.get('Location') or '')


def test_dashboard_requires_login(client):
    r = client.get('/')
    assert r.status_code == 302
    assert 'login' in (r.headers.get('Location') or '').lower()


def test_post_logout_sem_sessao_redireciona_login(client):
    r = client.post('/logout')
    assert r.status_code == 302
    assert 'login' in (r.headers.get('Location') or '').lower()


def test_post_logout_when_logged_in(admin_client):
    r = admin_client.post('/logout')
    assert r.status_code == 302
