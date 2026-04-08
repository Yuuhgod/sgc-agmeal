import os
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
from database import db, Associado, Usuario
from sqlalchemy import extract
from flask import make_response 
from weasyprint import HTML

app = Flask(__name__)
app.secret_key = "agmeal_secreta_2026" 

basedir = os.path.abspath(os.path.dirname(__file__))
data_dir = os.path.join(basedir, '..', 'data')
os.makedirs(data_dir, exist_ok=True) 

db_path = os.path.join(data_dir, 'sgc.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Mantenha apenas isso:
with app.app_context():
    db.create_all()

@app.before_request
def verificar_primeiro_acesso():
    # Ignora a verificação se o usuário já estiver na rota de setup ou carregando imagens/css
    if request.endpoint in ['setup', 'static']:
        return

    # Se não houver nenhum usuário no banco, obriga a passar pelo setup
    if Usuario.query.count() == 0:
        return redirect(url_for('setup'))

# --- DECORATOR DE SEGURANÇA ---
# Essa função verifica se o usuário está na sessão antes de acessar uma rota
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- ROTAS DE AUTENTICAÇÃO ---

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    # Proteção extra: se já existir admin, não deixa acessar essa tela nunca mais
    if Usuario.query.count() > 0:
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username']
        senha = request.form['senha']
        palavra = request.form['palavra_recuperacao']

        novo_admin = Usuario(username=username, palavra_recuperacao=palavra)
        novo_admin.set_senha(senha)
        
        db.session.add(novo_admin)
        db.session.commit()
        
        flash('Instalação concluída! Faça login com seu novo usuário.', 'success')
        return redirect(url_for('login'))

    return render_template('setup.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    # Se já estiver logado, manda direto pro painel
    if 'usuario_id' in session:
        return redirect(url_for('dashboard')) # <--- CORRIGIDO AQUI

    if request.method == 'POST':
        username = request.form['username']
        senha = request.form['senha']
        
        usuario = Usuario.query.filter_by(username=username).first()
        
        if usuario and usuario.check_senha(senha):
            session['usuario_id'] = usuario.id
            session['username'] = usuario.username
            return redirect(url_for('dashboard')) # <--- CORRIGIDO AQUI
        else:
            flash('Usuário ou senha incorretos.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear() # Limpa a sessão
    return redirect(url_for('login'))

@app.route('/esqueci_senha', methods=['GET', 'POST'])
def esqueci_senha():
    if request.method == 'POST':
        username = request.form['username']
        palavra = request.form['palavra_recuperacao']
        nova_senha = request.form['nova_senha']
        
        # Busca o usuário no banco
        usuario = Usuario.query.filter_by(username=username).first()
        
        # Verifica se o usuário existe e se a palavra de recuperação bate
        if usuario and usuario.palavra_recuperacao == palavra:
            
            # --- NOVA VALIDAÇÃO DE SEGURANÇA ---
            # Verifica se a nova senha digitada é igual à senha atual
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à senha atual.', 'warning')
                return redirect(url_for('esqueci_senha'))
            # -----------------------------------

            usuario.set_senha(nova_senha) # Salva a nova senha
            db.session.commit()
            flash('Senha alterada com sucesso! Você já pode fazer login.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Usuário ou Palavra de Recuperação incorretos.', 'danger')

    return render_template('esqueci_senha.html')

# --- ROTAS DO SISTEMA (PROTEGIDAS) ---

@app.route('/')
@login_required
def dashboard():
    # Consulta rápida no banco para saber o total de associados cadastrados
    total_associados = Associado.query.count()
    return render_template('dashboard.html', username=session.get('username'), total=total_associados)


@app.route('/cadastro', methods=['GET', 'POST'])
@login_required
def cadastro():
    # Se o método for POST, ele tenta salvar no banco
    if request.method == 'POST':
        try:
            cpf_limpo = request.form['cpf'].strip()
            novo_associado = Associado(
                nome=request.form['nome'],
                matricula=request.form['matricula'],
                rg=request.form['rg'],
                cpf=cpf_limpo,
                endereco=request.form['endereco'],
                data_nascimento=datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date(),
                email=request.form['email'],
                data_admissao=datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date(),
                dependentes=request.form.get('dependentes', '')
            )
            db.session.add(novo_associado)
            db.session.commit()
            flash('Associado cadastrado com sucesso!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Erro ao cadastrar. Verifique se CPF ou Matrícula já existem.', 'danger')
            
        return redirect(url_for('cadastro'))

    # Se for GET (apenas acessar a página), ele mostra o formulário
    return render_template('cadastro.html', username=session.get('username'))

@app.route('/buscar', methods=['GET', 'POST'])
@login_required
def buscar():
    resultados = None
    
    if request.method == 'POST':
        # Pega os dados digitados nos filtros
        nome_busca = request.form.get('nome')
        matricula_busca = request.form.get('matricula')
        ano_busca = request.form.get('ano')

        # Inicia uma query (consulta) base
        query = Associado.query

        # Aplica os filtros apenas se o usuário tiver digitado algo neles
        if nome_busca:
            # .ilike faz a busca parcial ignorando maiúsculas/minúsculas
            query = query.filter(Associado.nome.ilike(f'%{nome_busca}%'))
        
        if matricula_busca:
            # Busca exata pela matrícula
            query = query.filter(Associado.matricula == matricula_busca)
            
        if ano_busca:
            # Extrai apenas o ano da data_admissao no banco e compara
            query = query.filter(extract('year', Associado.data_admissao) == int(ano_busca))

        # Executa a busca e guarda os resultados
        resultados = query.all()

        if not resultados:
            flash('Nenhum registro encontrado com estes filtros.', 'warning')

    return render_template('buscar.html', username=session.get('username'), resultados=resultados)

@app.route('/exportar_pdf', methods=['POST'])
@login_required
def exportar_pdf():
    # Pega os mesmos dados do filtro que estavam na tela
    nome_busca = request.form.get('nome_export', '')
    matricula_busca = request.form.get('matricula_export', '')
    ano_busca = request.form.get('ano_export', '')

    query = Associado.query

    if nome_busca:
        query = query.filter(Associado.nome.ilike(f'%{nome_busca}%'))
    if matricula_busca:
        query = query.filter(Associado.matricula == matricula_busca)
    if ano_busca:
        query = query.filter(extract('year', Associado.data_admissao) == int(ano_busca))

    resultados = query.all()

    if not resultados:
        flash('Nenhum dado para exportar.', 'warning')
        return redirect(url_for('buscar'))

    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
    # Renderiza o HTML invisível (apenas para o WeasyPrint ler)
    html_renderizado = render_template('pdf_relatorio.html', resultados=resultados, now=data_geracao)
    
    # O WeasyPrint transforma o HTML em PDF
    pdf = HTML(string=html_renderizado).write_pdf()

    # Prepara o arquivo para ser baixado pelo navegador
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=relatorio_agmeal.pdf'
    # Nota: 'inline' abre no navegador. Se quiser forçar o download, troque por 'attachment'

    return response

@app.route('/exportar_ficha/<matricula>')
@login_required
def exportar_ficha(matricula):
    # Busca no banco apenas o associado com essa matrícula exata
    associado = Associado.query.filter_by(matricula=matricula).first()

    if not associado:
        flash('Associado não encontrado.', 'danger')
        return redirect(url_for('buscar'))

    # Criamos a string com a data e hora atual
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    # Usamos o MESMO template do relatório geral.
    # O truque aqui é colocar o associado dentro de colchetes [associado] 
    # para que o loop {% for %} do HTML funcione perfeitamente com 1 pessoa só.
    html_renderizado = render_template(
        'pdf_relatorio.html', 
        resultados=[associado], 
        now=data_geracao
    )
    
    # O WeasyPrint transforma em PDF
    pdf = HTML(string=html_renderizado).write_pdf()

    # Prepara o arquivo para ser visualizado no navegador
    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    
    # Nome do arquivo personalizado com a matrícula!
    response.headers['Content-Disposition'] = f'inline; filename=ficha_{associado.matricula}.pdf'

    return response

@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    # Busca o associado no banco pela ID única dele
    associado = Associado.query.get_or_404(id)

    if request.method == 'POST':
        try:
            # Puxa os dados novos digitados no formulário
            associado.nome = request.form['nome']
            associado.matricula = request.form['matricula']
            associado.rg = request.form['rg']
            associado.cpf = request.form['cpf'].strip() # Já aplicando a nossa regra de limpeza
            associado.endereco = request.form['endereco']
            associado.data_nascimento = datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
            associado.email = request.form['email']
            associado.data_admissao = datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            associado.dependentes = request.form.get('dependentes', '')

            # Salva no banco de dados
            db.session.commit()
            flash('Cadastro atualizado com sucesso!', 'success')
            
            # Redireciona de volta para a busca para você ver o resultado
            return redirect(url_for('buscar'))
            
        except Exception as e:
            db.session.rollback()
            flash('Erro ao atualizar. Verifique se o novo CPF ou Matrícula já existem no sistema.', 'danger')

    # Se for GET (quando você clica no botão "Editar" na tabela), ele abre a tela
    return render_template('editar.html', username=session.get('username'), associado=associado)

@app.route('/excluir/<int:id>')
@login_required
def excluir(id):
    # Busca o associado ou retorna 404 se não existir
    associado = Associado.query.get_or_404(id)
    
    try:
        db.session.delete(associado)
        db.session.commit()
        flash(f'O registro de {associado.nome} foi excluído com sucesso.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('Erro ao tentar excluir o registro.', 'danger')
    
    return redirect(url_for('buscar'))

@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    # Puxa o administrador logado atualmente
    usuario = Usuario.query.get(session['usuario_id'])
    
    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        novo_username = request.form['username'].strip()
        nova_senha = request.form.get('nova_senha', '').strip()

        # Trava de segurança: Exige a senha atual para fazer alterações
        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta. Nenhuma alteração foi salva.', 'danger')
            return redirect(url_for('perfil'))

        # Se o usuário quis mudar o próprio nome (login)
        if novo_username != usuario.username:
            # Verifica se já não tem outro admin usando esse mesmo nome
            existente = Usuario.query.filter_by(username=novo_username).first()
            if existente:
                flash('Este nome de usuário já está em uso.', 'warning')
                return redirect(url_for('perfil'))
            
            usuario.username = novo_username
            session['username'] = novo_username # Atualiza o nome que aparece na tela

        # Se o usuário digitou algo no campo de "Nova Senha"
        if nova_senha:
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à atual.', 'warning')
                return redirect(url_for('perfil'))
            usuario.set_senha(nova_senha)

        db.session.commit()
        flash('Perfil administrativo atualizado com sucesso!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('perfil.html', username=session.get('username'), usuario=usuario)


@app.route('/seguranca', methods=['GET', 'POST'])
@login_required
def seguranca():
    usuario = Usuario.query.get(session['usuario_id'])
    
    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        nova_palavra = request.form['nova_palavra'].strip()

        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta.', 'danger')
            return redirect(url_for('seguranca'))

        usuario.palavra_recuperacao = nova_palavra
        db.session.commit()
        flash('Frase de segurança atualizada com sucesso!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('seguranca.html', username=session.get('username'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)