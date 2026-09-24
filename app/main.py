"""Ponto de entrada do SGC-AGMEAL (gunicorn main:app, FLASK_APP=main:app).

O app é criado em nucleo.py; cada módulo rotas_*.py registra as suas rotas nele ao ser
importado. Os nomes das rotas (endpoints) não mudaram com a divisão em módulos.
"""

import os

import rotas_associados  # noqa: F401 (registra as rotas)
import rotas_auth  # noqa: F401
import rotas_backup  # noqa: F401
import rotas_carteirinha  # noqa: F401
import rotas_importacao  # noqa: F401
import rotas_lgpd  # noqa: F401
import rotas_usuarios  # noqa: F401
from nucleo import (  # noqa: F401 (usados pelo gunicorn, Flask CLI e testes)
    app,
    db,
    limiter,
)

if __name__ == '__main__':
    _debug = os.environ.get('FLASK_DEBUG', '').lower() in ('1', 'true', 'yes')
    _host = os.environ.get('FLASK_HOST', '127.0.0.1')
    _port = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(debug=_debug, host=_host, port=_port)
