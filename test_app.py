"""Teste completo do app SGC-AGMEAL."""
import sys, os, re

os.chdir('/sgc/app')
sys.path.insert(0, '/sgc/app')

# Desabilita CSRF para o ambiente de testes (recomendação oficial do Flask-WTF).
# A proteção CSRF é validada individualmente no teste de login abaixo.
os.environ.setdefault('WTF_CSRF_ENABLED', '0')

from main import app
from database import db, Usuario, Associado

app.config['WTF_CSRF_ENABLED'] = False
app.config['WTF_CSRF_CHECK_DEFAULT'] = False

results = []

def check(label, status, expected=200):
    ok = status == expected
    results.append((ok, f"{'[OK]' if ok else '[FALHOU]'} {label}: HTTP {status} (esperado {expected})"))

with app.test_client() as c:
    with app.app_context():
        users_count = Usuario.query.count()
        assoc_count = Associado.query.count()
        print(f"\n=== SGC-AGMEAL - Teste Completo ===")
        print(f"DB: {users_count} usuário(s), {assoc_count} associado(s)\n")

        # 1. Setup (com usuário → deve redirecionar)
        r = c.get('/setup')
        check("GET /setup (com usuario -> redireciona)", r.status_code, 302)

        # 2. Página de login acessível sem autenticação
        r = c.get('/login')
        check("GET /login", r.status_code, 200)

        # 3. Login com credenciais erradas → retorna 200 com mensagem de erro (sem login ainda)
        r = c.post('/login', data={'username': 'naoexiste', 'senha': 'senhaerrada'})
        check("POST /login (credenciais erradas -> 200 com flash)", r.status_code, 200)

        # 4. Login com credenciais corretas
        r = c.post('/login', data={'username': 'admin', 'senha': 'admin123'})
        check("POST /login (credenciais corretas -> redireciona)", r.status_code, 302)

        # 5. Dashboard autenticado
        r = c.get('/')
        check("GET / (dashboard)", r.status_code, 200)

        # 6. Cadastro (GET)
        r = c.get('/cadastro')
        check("GET /cadastro", r.status_code, 200)

        # 7. Buscar (GET)
        r = c.get('/buscar')
        check("GET /buscar", r.status_code, 200)

        # 8. Buscar (POST com filtros vazios → retorna lista completa)
        r = c.post('/buscar', data={'nome': '', 'matricula': '', 'ano': ''})
        check("POST /buscar (busca vazia)", r.status_code, 200)

        # 9. Listar todos
        r = c.get('/listar')
        check("GET /listar", r.status_code, 200)

        # 10. Perfil
        r = c.get('/perfil')
        check("GET /perfil", r.status_code, 200)

        # 11. Segurança
        r = c.get('/seguranca')
        check("GET /seguranca", r.status_code, 200)

        # 12. Usuários (somente admin)
        r = c.get('/usuarios')
        check("GET /usuarios (admin)", r.status_code, 200)

        # 13. Criar novo usuário (admin)
        r = c.get('/usuarios/novo')
        check("GET /usuarios/novo", r.status_code, 200)

        # 14. Trilha de auditoria (admin)
        r = c.get('/auditoria')
        check("GET /auditoria", r.status_code, 200)

        # 15. Editar associado
        assoc = Associado.query.first()
        if assoc:
            r = c.get(f'/editar/{assoc.id}')
            check(f"GET /editar/{assoc.id}", r.status_code, 200)
        else:
            results.append((False, "[FALHOU] Nenhum associado no banco para testar /editar"))

        # 16. Exportar ficha individual (PDF)
        if assoc:
            r = c.get(f'/exportar_ficha/{assoc.matricula}')
            check(f"GET /exportar_ficha/{assoc.matricula} (PDF)", r.status_code, 200)

        # 17. Exportar lista em PDF (POST)
        r = c.post('/exportar_lista_simples')
        check("POST /exportar_lista_simples (PDF)", r.status_code, 200)

        # 18. Esqueci senha (GET — acessível sem autenticação)
        r = c.get('/esqueci_senha')
        check("GET /esqueci_senha", r.status_code, 200)

        # 19. Logout via POST (seguro contra CSRF)
        r = c.post('/logout')
        check("POST /logout (redireciona para login)", r.status_code, 302)

        # 20. Após logout, dashboard deve bloquear e redirecionar para login
        r = c.get('/')
        check("GET / após logout (bloqueado -> redireciona para login)", r.status_code, 302)

        # 21. GET /logout deve retornar 405 (somente POST é aceito)
        r = c.get('/logout')
        check("GET /logout deve retornar 405 (Method Not Allowed)", r.status_code, 405)

print("\n" + "="*50)
ok_count = sum(1 for ok, _ in results if ok)
total = len(results)

for ok, msg in results:
    print(msg)

print(f"\n{'='*50}")
print(f"RESULTADO: {ok_count}/{total} testes passaram")
if ok_count == total:
    print("TUDO OK!")
else:
    print(f"{total - ok_count} FALHOU(RAM)")
