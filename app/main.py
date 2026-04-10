import os
import re
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, flash, session
import base64
from database import db, Associado, Usuario
from sqlalchemy import extract
from flask import make_response
from weasyprint import HTML
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "agmeal_secreta_2026"

basedir = os.path.abspath(os.path.dirname(__file__))
data_dir = os.path.join(basedir, '..', 'data')
os.makedirs(data_dir, exist_ok=True)

db_path = os.path.join(data_dir, 'sgc.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

UPLOAD_FOLDER = os.path.join(basedir, 'static', 'uploads', 'fotos')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

db.init_app(app)

with app.app_context():
    db.create_all()

def validar_cpf(cpf):
    # Remove tudo que não for número
    cpf = re.sub(r'\D', '', cpf)

    # Verifica tamanho e se tem números repetidos (ex: 11111111111)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False

    # Calcula o primeiro dígito verificador
    soma = sum(int(cpf[i]) * (10 - i) for i in range(9))
    digito1 = (soma * 10) % 11
    if digito1 >= 10: digito1 = 0
    if digito1 != int(cpf[9]): return False

    # Calcula o segundo dígito verificador
    soma = sum(int(cpf[i]) * (11 - i) for i in range(10))
    digito2 = (soma * 10) % 11
    if digito2 >= 10: digito2 = 0
    if digito2 != int(cpf[10]): return False

    return True

@app.before_request
def verificar_primeiro_acesso():
    if request.endpoint in ['setup', 'static']:
        return
    if Usuario.query.count() == 0:
        return redirect(url_for('setup'))

@app.after_request
def add_header(response):
    """
    Impede que o navegador guarde a página na memória.
    Assim, ao clicar em "Voltar" após o logout, ele é obrigado
    a pedir a página pro servidor (que vai negar o acesso).
    """
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario_id' not in session:
            flash('Por favor, faça login para acessar o sistema.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/setup', methods=['GET', 'POST'])
def setup():
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
    if 'usuario_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username']
        senha = request.form['senha']

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.check_senha(senha):
            session['usuario_id'] = usuario.id
            session['username'] = usuario.username
            return redirect(url_for('dashboard'))
        else:
            flash('Usuário ou senha incorretos.', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/esqueci_senha', methods=['GET', 'POST'])
def esqueci_senha():
    if request.method == 'POST':
        username = request.form['username']
        palavra = request.form['palavra_recuperacao']
        nova_senha = request.form['nova_senha']

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.palavra_recuperacao == palavra:
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à senha atual.', 'warning')
                return redirect(url_for('esqueci_senha'))

            usuario.set_senha(nova_senha)
            db.session.commit()
            flash('Senha alterada com sucesso! Você já pode fazer login.', 'success')
            return redirect(url_for('login'))
        else:
            flash('Usuário ou Palavra de Recuperação incorretos.', 'danger')

    return render_template('esqueci_senha.html')

@app.route('/')
@login_required
def dashboard():
    total_associados = Associado.query.count()
    return render_template('dashboard.html', username=session.get('username'), total=total_associados)


@app.route('/cadastro', methods=['GET', 'POST'])
@login_required
def cadastro():
    if request.method == 'POST':
        try:
            cpf_limpo = request.form['cpf'].strip()

            # --- SE O CPF FOR INVÁLIDO ---
            if not validar_cpf(cpf_limpo):
                flash('O CPF digitado é matematicamente inválido.', 'danger')
                # MUDANÇA: Retorna a página com os dados ao invés de redirecionar
                return render_template('cadastro.html', username=session.get('username'))

            foto_b64 = request.form.get('foto_base64')
            nome_arquivo = None

            if foto_b64:
                header, encoded = foto_b64.split(",", 1)
                extensao = "png" if "image/png" in header else "jpg"
                nome_arquivo = secure_filename(f"{request.form['matricula']}_perfil.{extensao}")
                caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)

                with open(caminho_salvar, "wb") as fh:
                    fh.write(base64.b64decode(encoded))

            novo_associado = Associado(
                nome=request.form['nome'],
                matricula=request.form['matricula'],
                rg=request.form['rg'],
                cpf=cpf_limpo,
                telefone=request.form.get('telefone', ''),
                telefone_whatsapp=request.form.get('telefone_whatsapp', ''),
                foto_perfil=nome_arquivo,
                endereco=request.form['endereco'],
                data_nascimento=datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date(),
                email=request.form['email'],
                data_admissao=datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date(),
                dependentes=request.form.get('dependentes', '')
            )
            db.session.add(novo_associado)
            db.session.commit()
            flash('Associado cadastrado com sucesso!', 'success')

            # MANTÉM REDIRECT APENAS NO SUCESSO (Para limpar a tela para o próximo cadastro)
            return redirect(url_for('cadastro'))

        except Exception as e:
            db.session.rollback()
            flash('Erro ao cadastrar. Verifique se CPF ou Matrícula já existem.', 'danger')
            # MUDANÇA: Retorna a página com os dados
            return render_template('cadastro.html', username=session.get('username'))

    return render_template('cadastro.html', username=session.get('username'))

@app.route('/buscar', methods=['GET', 'POST'])
@login_required
def buscar():
    resultados = None

    if request.method == 'POST':
        nome_busca = request.form.get('nome')
        matricula_busca = request.form.get('matricula')
        ano_busca = request.form.get('ano')

        query = Associado.query

        if nome_busca:
            query = query.filter(Associado.nome.ilike(f'%{nome_busca}%'))
        if matricula_busca:
            query = query.filter(Associado.matricula == matricula_busca)
        if ano_busca:
            query = query.filter(extract('year', Associado.data_admissao) == int(ano_busca))

        resultados = query.all()

        if not resultados:
            flash('Nenhum registro encontrado com estes filtros.', 'warning')

    return render_template('buscar.html', username=session.get('username'), resultados=resultados)

@app.route('/exportar_pdf', methods=['POST'])
@login_required
def exportar_pdf():
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
    html_renderizado = render_template('pdf_relatorio.html', resultados=resultados, now=data_geracao)

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=relatorio_agmeal.pdf'

    return response

@app.route('/exportar_ficha/<matricula>')
@login_required
def exportar_ficha(matricula):
    associado = Associado.query.filter_by(matricula=matricula).first()

    if not associado:
        flash('Associado não encontrado.', 'danger')
        return redirect(url_for('buscar'))

    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    html_renderizado = render_template(
        'pdf_relatorio.html',
        resultados=[associado],
        now=data_geracao
    )

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'inline; filename=ficha_{associado.matricula}.pdf'

    return response

@app.route('/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar(id):
    associado = Associado.query.get_or_404(id)

    if request.method == 'POST':
        try:
            cpf_limpo = request.form['cpf'].strip()

            # --- NOVA TRAVA DE CPF ---
            if not validar_cpf(cpf_limpo):
                flash('O CPF digitado é matematicamente inválido.', 'danger')
                return redirect(url_for('editar', id=id))
            # -------------------------

            associado.nome = request.form['nome']
            associado.matricula = request.form['matricula']
            associado.rg = request.form['rg']
            associado.cpf = cpf_limpo # Pega o CPF que já passou pela limpeza
            associado.telefone = request.form.get('telefone', '')
            associado.telefone_whatsapp = request.form.get('telefone_whatsapp', '')
            associado.endereco = request.form['endereco']
            associado.data_nascimento = datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
            associado.email = request.form['email']
            associado.data_admissao = datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            associado.dependentes = request.form.get('dependentes', '')

            foto = request.files.get('foto_perfil')
            if foto and allowed_file(foto.filename):
                nome_arquivo = secure_filename(f"{associado.matricula}_{foto.filename}")
                caminho_salvar = os.path.join(app.config['UPLOAD_FOLDER'], nome_arquivo)
                foto.save(caminho_salvar)
                associado.foto_perfil = nome_arquivo

            db.session.commit()
            flash('Cadastro atualizado com sucesso!', 'success')
            return redirect(url_for('buscar'))

        except Exception as e:
            db.session.rollback()
            flash('Erro ao atualizar. Verifique se o novo CPF ou Matrícula já existem no sistema.', 'danger')

    return render_template('editar.html', username=session.get('username'), associado=associado)

@app.route('/excluir/<int:id>')
@login_required
def excluir(id):
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
    usuario = Usuario.query.get(session['usuario_id'])

    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        novo_username = request.form['username'].strip()
        nova_senha = request.form.get('nova_senha', '').strip()

        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta. Nenhuma alteração foi salva.', 'danger')
            return redirect(url_for('perfil'))

        if novo_username != usuario.username:
            existente = Usuario.query.filter_by(username=novo_username).first()
            if existente:
                flash('Este nome de usuário já está em uso.', 'warning')
                return redirect(url_for('perfil'))

            usuario.username = novo_username
            session['username'] = novo_username

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

@app.route('/listar')
@login_required
def listar_todos():
    associados = Associado.query.order_by(Associado.nome).all()
    return render_template('listar.html', username=session.get('username'), associados=associados)

@app.route('/exportar_lista_simples', methods=['POST', 'GET'])
@login_required
def exportar_lista_simples():
    associados = Associado.query.order_by(Associado.nome).all()
    data_geracao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')

    html_renderizado = render_template('pdf_lista_simples.html', associados=associados, now=data_geracao)

    pdf = HTML(string=html_renderizado, base_url=request.url_root).write_pdf()

    response = make_response(pdf)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = 'inline; filename=lista_associados.pdf'

    return response

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
