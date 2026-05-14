"""Fixtures partilhadas: app Flask, cliente HTTP e sessão de administrador."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault('WTF_CSRF_ENABLED', '0')


@pytest.fixture(scope='session')
def flask_app():
    """Uma única instância da app por corrida de testes (mesmo modelo que test_app.py)."""
    from main import app as application
    import main as main_module

    application.config['TESTING'] = True
    application.config['WTF_CSRF_ENABLED'] = False
    application.config['WTF_CSRF_CHECK_DEFAULT'] = False
    # Vários testes fazem POST /login; o limiter em memória contaria 429.
    main_module.limiter.enabled = False
    yield application


@pytest.fixture
def client(flask_app):
    return flask_app.test_client()


@pytest.fixture
def admin_credentials(client):
    """Garante pelo menos um utilizador admin (setup guiado na primeira corrida)."""
    with client.application.app_context():
        from database import Usuario

        if Usuario.query.count() == 0:
            resp = client.post(
                '/setup',
                data={
                    'username': 'admin',
                    'senha': 'admin123',
                    'palavra_recuperacao': 'frase_teste_seg',
                },
            )
            assert resp.status_code == 302, 'POST /setup deveria redirecionar após criar admin'

    return {'username': 'admin', 'senha': 'admin123'}


@pytest.fixture
def admin_client(client, admin_credentials):
    """Cliente autenticado como administrador."""
    resp = client.post('/login', data=admin_credentials)
    assert resp.status_code == 302, 'Login admin deveria redirecionar'
    yield client
