# SGC - AGMEAL (Sistema de Gestão de Cadastros)

Um sistema completo desenvolvido em **Python (Flask)** para a gestão de registros de associados, rodando em contêineres **Docker** com **Nginx** como Proxy Reverso.

## 🚀 Funcionalidades
* **Autenticação:** Sistema de login seguro com setup guiado de primeira execução.
* **Recuperação de Acesso:** Fluxo de redefinição de senha via Frase de Segurança.
* **Gestão de Associados (CRUD):** Cadastro, Busca, Edição e Exclusão.
* **Geração de PDF:** Fichas individuais e relatórios em lote utilizando `WeasyPrint`.
* **Interface:** Front-end responsivo construído com Bootstrap 5 e ícones FontAwesome.
* **Infraestrutura:** Containerizado com Docker e servido via Nginx.

## 🛠️ Tecnologias Utilizadas
* **Backend:** Python 3.12, Flask, SQLAlchemy
* **Frontend:** HTML5, CSS3, Bootstrap 5, JavaScript
* **Infraestrutura:** Docker, Docker Compose, Nginx, SQLite

## ⚙️ Como Executar o Projeto

1. Clone o repositório:
   ```bash
   git clone [https://github.com/yuuhgod/sgc-agmeal.git](https://github.com/yuuhgod/sgc-agmeal.git)