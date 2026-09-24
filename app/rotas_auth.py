"""Rotas de autenticação: setup inicial, login/logout, recuperação e troca de senha, perfil."""

import time

from flask import flash, redirect, render_template, request, session, url_for

from database import (
    ACAO_AUTH_LOGIN,
    ACAO_AUTH_LOGIN_FALHOU,
    ACAO_AUTH_LOGOUT,
    ACAO_AUTH_RECUPERACAO,
    ACAO_AUTH_RECUPERACAO_FALHOU,
    ACAO_USUARIO_PALAVRA,
    ACAO_USUARIO_PERFIL,
    ACAO_USUARIO_SENHA_TROCADA,
    ROLE_ADMIN,
    Usuario,
    db,
)
from nucleo import (
    _login_bloqueado_por_ip,
    _recuperacao_bloqueada_por_ip,
    app,
    limiter,
    login_required,
    registrar_auditoria,
)


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if Usuario.query.count() > 0:
        return redirect(url_for('login'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        senha = request.form['senha']
        palavra = request.form['palavra_recuperacao'].strip()

        if not username or not palavra:
            flash('Usuário e frase de segurança são obrigatórios.', 'danger')
            return render_template('setup.html')

        if len(senha) < 8:
            flash('A senha deve ter no mínimo 8 caracteres.', 'danger')
            return render_template('setup.html')

        # O usuário criado no setup é sempre administrador.
        novo_admin = Usuario(username=username, role=ROLE_ADMIN)
        novo_admin.set_senha(senha)
        novo_admin.set_palavra_recuperacao(palavra)

        db.session.add(novo_admin)
        db.session.commit()

        flash('Instalação concluída! Faça login com seu novo usuário.', 'success')
        return redirect(url_for('login'))

    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])
def login():
    if 'usuario_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form['username'].strip()
        senha = request.form['senha']

        if _login_bloqueado_por_ip(request.remote_addr):
            app.logger.warning(
                'Login bloqueado por excesso de tentativas: ip=%s usuario=%s',
                request.remote_addr, username,
            )
            flash(
                'Muitas tentativas de login malsucedidas deste computador. '
                'Aguarde alguns minutos e tente novamente.',
                'danger',
            )
            return render_template('login.html')

        usuario = Usuario.query.filter_by(username=username).first()

        if usuario and usuario.check_senha(senha) and not usuario.ativo:
            # Só revelado a quem acertou a senha: não serve para descobrir contas.
            app.logger.warning('Login recusado (conta desativada): %s', username)
            registrar_auditoria(
                ACAO_AUTH_LOGIN_FALHOU,
                descricao=f'Conta desativada: "{username}"',
                usuario_id=usuario.id,
                usuario_username=usuario.username,
                commit=True,
            )
            flash('Esta conta está desativada. Procure um administrador.', 'danger')
            return render_template('login.html')

        if usuario and usuario.check_senha(senha):
            session.clear()
            session['usuario_id'] = usuario.id
            session['username'] = usuario.username
            session['role'] = usuario.role
            session['ultimo_acesso'] = int(time.time())
            session.permanent = True
            app.logger.info('Login bem-sucedido: %s (role=%s)', username, usuario.role)
            registrar_auditoria(
                ACAO_AUTH_LOGIN,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'Login de {usuario.username}',
                usuario_id=usuario.id,
                usuario_username=usuario.username,
                commit=True,
            )
            return redirect(url_for('dashboard'))

        app.logger.warning('Tentativa de login falha para usuário: %s', username)
        registrar_auditoria(
            ACAO_AUTH_LOGIN_FALHOU,
            descricao=f'Tentativa com usuário "{username}"',
            usuario_id=None,
            usuario_username=username or '(vazio)',
            commit=True,
        )
        flash('Usuário ou senha incorretos.', 'danger')

    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    if 'usuario_id' in session:
        registrar_auditoria(
            ACAO_AUTH_LOGOUT,
            entidade='usuario',
            entidade_id=session.get('usuario_id'),
            descricao=f'Logout de {session.get("username")}',
            commit=True,
        )
    session.clear()
    return redirect(url_for('login'))


@app.route('/esqueci_senha', methods=['GET', 'POST'])
@limiter.limit("5 per minute", methods=["POST"])
def esqueci_senha():
    if request.method == 'POST':
        username = request.form['username'].strip()
        palavra = request.form['palavra_recuperacao']
        nova_senha = request.form['nova_senha']

        if len(nova_senha) < 8:
            flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
            return render_template('esqueci_senha.html')

        if _recuperacao_bloqueada_por_ip(request.remote_addr):
            app.logger.warning(
                'Recuperação bloqueada por excesso de tentativas: ip=%s usuario=%s',
                request.remote_addr, username,
            )
            flash(
                'Muitas tentativas de recuperação malsucedidas deste computador. '
                'Aguarde alguns minutos e tente novamente.',
                'danger',
            )
            return render_template('esqueci_senha.html')

        usuario = Usuario.query.filter_by(username=username).first()

        # Frases novas são gravadas sem espaços nas pontas; as antigas podem tê-los.
        palavra_ok = usuario is not None and usuario.ativo and (
            usuario.verificar_palavra_recuperacao(palavra)
            or (palavra.strip() != palavra and usuario.verificar_palavra_recuperacao(palavra.strip()))
        )

        if palavra_ok:
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à senha atual.', 'warning')
                return redirect(url_for('esqueci_senha'))

            usuario.migrar_palavra_recuperacao_se_legado(palavra)
            usuario.set_senha(nova_senha)
            usuario.trocar_senha = False
            registrar_auditoria(
                ACAO_AUTH_RECUPERACAO,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'{usuario.username} redefiniu a senha pela frase de segurança',
                usuario_id=usuario.id,
                usuario_username=usuario.username,
            )
            db.session.commit()
            app.logger.info('Senha redefinida via palavra de recuperação: %s', username)
            flash('Senha alterada com sucesso! Você já pode fazer login.', 'success')
            return redirect(url_for('login'))

        app.logger.warning('Recuperação falha para usuário: %s', username)
        registrar_auditoria(
            ACAO_AUTH_RECUPERACAO_FALHOU,
            descricao=f'Tentativa de recuperação com usuário "{username}"',
            usuario_id=None,
            usuario_username=username or '(vazio)',
            commit=True,
        )
        flash('Usuário ou Palavra de Recuperação incorretos.', 'danger')

    return render_template('esqueci_senha.html')


@app.route('/perfil', methods=['GET', 'POST'])
@login_required
def perfil():
    usuario = db.session.get(Usuario, session['usuario_id'])

    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        novo_username = request.form['username'].strip()
        nova_senha = request.form.get('nova_senha', '').strip()

        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta. Nenhuma alteração foi salva.', 'danger')
            return redirect(url_for('perfil'))

        username_anterior = usuario.username
        senha_alterada = False
        username_alterado = False

        if novo_username != usuario.username:
            existente = Usuario.query.filter_by(username=novo_username).first()
            if existente:
                flash('Este nome de usuário já está em uso.', 'warning')
                return redirect(url_for('perfil'))
            username_alterado = True

        if nova_senha:
            if len(nova_senha) < 8:
                flash('A nova senha deve ter no mínimo 8 caracteres.', 'warning')
                return redirect(url_for('perfil'))
            if usuario.check_senha(nova_senha):
                flash('A nova senha não pode ser igual à atual.', 'warning')
                return redirect(url_for('perfil'))
            usuario.set_senha(nova_senha)
            usuario.trocar_senha = False
            senha_alterada = True

        # Aplica mudança de username somente após passar todas as validações.
        if username_alterado:
            usuario.username = novo_username

        mudancas = []
        if username_alterado:
            mudancas.append(f'usuário: "{username_anterior}" → "{novo_username}"')
        if senha_alterada:
            mudancas.append('senha alterada')
        if mudancas:
            registrar_auditoria(
                ACAO_USUARIO_PERFIL,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'Perfil de {usuario.username}',
                detalhes='\n'.join(mudancas),
            )
        db.session.commit()
        # Sincroniza a sessão com o username persistido no banco.
        session['username'] = usuario.username
        flash('Perfil administrativo atualizado com sucesso!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('perfil.html', username=session.get('username'), usuario=usuario)


@app.route('/seguranca', methods=['GET', 'POST'])
@login_required
def seguranca():
    usuario = db.session.get(Usuario, session['usuario_id'])

    if request.method == 'POST':
        senha_atual = request.form['senha_atual']
        nova_palavra = request.form['nova_palavra'].strip()

        if not nova_palavra:
            flash('A frase de segurança não pode ficar em branco.', 'danger')
            return redirect(url_for('seguranca'))

        if not usuario.check_senha(senha_atual):
            flash('Senha atual incorreta.', 'danger')
            return redirect(url_for('seguranca'))

        usuario.set_palavra_recuperacao(nova_palavra)
        registrar_auditoria(
            ACAO_USUARIO_PALAVRA,
            entidade='usuario',
            entidade_id=usuario.id,
            descricao=f'{usuario.username} alterou a frase de segurança',
        )
        db.session.commit()
        flash('Frase de segurança atualizada com sucesso!', 'success')
        return redirect(url_for('dashboard'))

    return render_template('seguranca.html', username=session.get('username'))


@app.route('/trocar_senha', methods=['GET', 'POST'])
@login_required
def trocar_senha():
    """Troca obrigatória da senha provisória (definida por um admin ou na criação da conta)."""
    usuario = db.session.get(Usuario, session['usuario_id'])
    if not usuario.trocar_senha:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        nova = request.form.get('nova_senha', '')
        if len(nova) < 8:
            flash('A nova senha deve ter no mínimo 8 caracteres.', 'danger')
        elif nova != request.form.get('confirmacao', ''):
            flash('A confirmação não confere com a nova senha.', 'danger')
        elif usuario.check_senha(nova):
            flash('A nova senha não pode ser igual à senha provisória.', 'warning')
        else:
            usuario.set_senha(nova)
            usuario.trocar_senha = False
            registrar_auditoria(
                ACAO_USUARIO_SENHA_TROCADA,
                entidade='usuario',
                entidade_id=usuario.id,
                descricao=f'{usuario.username} criou a própria senha',
            )
            db.session.commit()
            flash('Senha criada com sucesso. Bem-vindo!', 'success')
            return redirect(url_for('dashboard'))

    return render_template('trocar_senha.html', username=session.get('username'))
