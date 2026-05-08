# SGC - AGMEAL (Sistema de Gestão de Cadastros)

Sistema desenvolvido em **Python (Flask + SQLAlchemy)** para a gestão de registros de associados, servido por **Gunicorn** em contêiner **Docker** e publicado via **Nginx** como Proxy Reverso.

## Funcionalidades
- **Autenticação:** login com setup guiado de primeira execução e rate limiting.
- **Recuperação de Acesso:** fluxo de redefinição de senha via Frase de Segurança.
- **Gestão de Associados (CRUD):** cadastro, busca, edição, exclusão e listagem paginada.
- **Fotos 3x4:** upload com crop client-side (Cropper.js), validação de tipo/tamanho no servidor e limpeza automática de fotos órfãs.
- **Geração de PDF:** fichas individuais e relatórios em lote utilizando `WeasyPrint`.
- **Interface:** front-end responsivo com Bootstrap 5 e FontAwesome.

## Tecnologias
- **Backend:** Python 3.12, Flask 3, SQLAlchemy, Flask-WTF, Flask-Limiter.
- **Servidor:** Gunicorn atrás de Nginx (com `ProxyFix` no Flask).
- **Banco:** SQLite em volume local (`data/sgc.db`).
- **Frontend:** HTML5, CSS3, Bootstrap 5, Cropper.js.
- **Infra:** Docker Compose, Nginx Alpine, healthcheck de serviço.

## Como Executar

### Opção 1 — Windows (recomendado para uso em PCs comuns)

1. Copie a pasta `sgc-agmeal` para o computador
2. Duplo clique em **`INSTALAR.bat`** e aceite o pedido de Administrador
3. O script faz tudo sozinho:
   - Instala WSL2 + Ubuntu (se necessário)
   - Copia o projeto e instala todas as dependências
   - Configura a URL amigável **`http://sgc.local`** (mapeia em `hosts` +
     redireciona porta 80 → 5000 via `netsh portproxy`)
   - Cria o atalho **SGC-AGMEAL** na Área de Trabalho (abre direto no navegador)
   - Configura **auto-start no boot** (script `.vbs` invisível em `shell:startup`)
4. Para usar: duplo clique em **SGC-AGMEAL** na Área de Trabalho

O servidor passa a iniciar **sozinho** toda vez que o PC ligar — o atalho só
abre o navegador na URL configurada.

> Na primeira instalação do WSL, o Windows pede para reiniciar o PC. Após
> reiniciar e criar usuário/senha no Ubuntu (janela que abre sozinha),
> execute `INSTALAR.bat` novamente para concluir.

#### Atalhos avançados (uso manual / manutenção)

Também são criados na Área de Trabalho:
- `SGC-Iniciar.bat` — força o início manual do servidor
- `SGC-Parar.bat` — encerra o servidor

### Opção 2 — Docker (servidor / desenvolvimento)

```bash
git clone https://github.com/yuuhgod/sgc-agmeal.git
cd sgc-agmeal
docker compose up -d --build
```

Depois acesse `http://localhost` e conclua a tela de **primeira configuração**.

### Opção 3 — Linux/WSL manual

```bash
cd sgc-agmeal
bash instalar.sh   # instala deps, gera scripts e atalhos
bash start.sh      # inicia o servidor
```

### Variáveis de Ambiente Suportadas
| Variável | Padrão | Descrição |
|---|---|---|
| `SECRET_KEY` | gerada em `data/.flask_secret` | Chave de sessão/CSRF. Defina uma fixa em produção. |
| `SESSION_COOKIE_SECURE` | `false` | Deixe `true` quando servir via HTTPS. |
| `GUNICORN_WORKERS` | `3` | Número de workers do Gunicorn. |
| `GUNICORN_TIMEOUT` | `60` | Timeout (s) por requisição. |
| `TZ` | `America/Maceio` | Fuso horário do contêiner. |

### Backup do Banco
O banco SQLite fica em `./data/sgc.db`. Copie periodicamente (ex.: cron diário) para outro disco. As fotos ficam em `./uploads/`.

## Estrutura
```
app/
  main.py            Rotas e configuração do app
  database.py        Modelos SQLAlchemy
  templates/         Jinja2
  static/            CSS, JS e imagens (inclui uploads/fotos)
data/                Banco SQLite e segredo de sessão
uploads/             Fotos de perfil dos associados (volume)
dockerfile
docker-compose.yml
nginx.conf
```

## Próximos Passos Recomendados
- Habilitar HTTPS no Nginx (Let's Encrypt / Caddy).
- Adicionar `Flask-Migrate` para gerenciar mudanças de schema.
- Criar testes com `pytest` cobrindo CRUD e autenticação.
- Refatorar rotas em Blueprints conforme o projeto crescer.
