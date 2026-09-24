"""Gestão de usuários (admin) e trilha de auditoria."""

from datetime import datetime, timedelta

from flask import flash, redirect, render_template, request, session, url_for

from database import (
    ACAO_USUARIO_CRIAR,
    ACAO_USUARIO_EDITAR,
    ACAO_USUARIO_EXCLUIR,
    ACAO_USUARIO_SENHA_REDEFINIDA,
    ACOES_ROTULOS,
    ROLE_ADMIN,
    ROLE_USUARIO,
    ROLES_VALIDAS,
    Auditoria,
    Usuario,
    db,
)
from nucleo import _contem_texto, admin_required, app, registrar_auditoria


@app.route('/usuarios')
@admin_required
def listar_usuarios():
    usuarios = Usuario.query.order_by(Usuario.username).all()
    return render_template(
        'usuarios.html',
        username=session.get('username'),
        usuarios=usuarios,
        usuario_id_atual=session.get('usuario_id'),
    )


@app.route('/usuarios/novo', methods=['GET', 'POST'])
@admin_required
def criar_usuario():
    if request.method == 'POST':
        novo_username = request.form['username'].strip()
        senha = request.form['senha']
        palavra = request.form['palavra_recuperacao'].strip()
        role = request.form.get('role', ROLE_USUARIO).strip()

        if role not in ROLES_VALIDAS:
            flash('Perfil inválido.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

        if not novo_username or not senha or not palavra:
            flash('Usuário, senha e palavra de recuperação são obrigatórios.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

        if len(senha) < 8:
            flash('A senha deve ter no mínimo 8 caracteres.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

        if Usuario.query.filter_by(username=novo_username).first():
            flash('Já existe um usuário com este nome.', 'warning')
            return render_template('usuario_novo.html', username=session.get('username'))

        novo = Usuario(username=novo_username, role=role)
        novo.set_senha(senha)
        # Padrão: quem cria a conta conhece a senha, então o usuário troca no primeiro acesso.
        novo.trocar_senha = request.form.get('trocar_senha') == '1'
        novo.set_palavra_recuperacao(palavra)

        try:
            db.session.add(novo)
            db.session.flush()
            registrar_auditoria(
                ACAO_USUARIO_CRIAR,
                entidade='usuario',
                entidade_id=novo.id,
                descricao=f'{novo_username} (perfil: {role})',
            )
            db.session.commit()
            app.logger.info(
                'Usuário criado por %s: %s (role=%s)',
                session.get('username'), novo_username, role,
            )
            flash(f'Usuário "{novo_username}" criado com sucesso.', 'success')
            return redirect(url_for('listar_usuarios'))
        except Exception:
            db.session.rollback()
            app.logger.exception('Erro ao criar usuário %s', novo_username)
            flash('Erro ao criar o usuário. Tente novamente.', 'danger')
            return render_template('usuario_novo.html', username=session.get('username'))

    return render_template('usuario_novo.html', username=session.get('username'))


def _usuario_admin_ativo_unico(usuario):
    """True se `usuario` é o único administrador ativo (não pode perder o acesso de admin)."""
    if usuario.role != ROLE_ADMIN or not usuario.ativo:
        return False
    return Usuario.query.filter_by(role=ROLE_ADMIN, ativo=True).count() <= 1


@app.route('/usuarios/<int:id>/editar', methods=['GET', 'POST'])
@admin_required
def editar_usuario(id):
    usuario = db.session.get(Usuario, id)
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('listar_usuarios'))
    eh_voce = usuario.id == session.get('usuario_id')

    if request.method == 'POST':
        if eh_voce:
            flash('Você não pode alterar o próprio perfil ou acesso. Peça a outro administrador.', 'warning')
            return redirect(url_for('editar_usuario', id=id))

        role = request.form.get('role', '').strip()
        ativo = request.form.get('ativo') == '1'
        if role not in ROLES_VALIDAS:
            flash('Perfil inválido.', 'danger')
            return redirect(url_for('editar_usuario', id=id))
        if _usuario_admin_ativo_unico(usuario) and (role != ROLE_ADMIN or not ativo):
            flash('Este é o único administrador ativo: não pode ser rebaixado nem desativado.', 'warning')
            return redirect(url_for('editar_usuario', id=id))

        mudancas = []
        if usuario.role != role:
            mudancas.append(f'perfil: "{usuario.role}" → "{role}"')
            usuario.role = role
        if usuario.ativo != ativo:
            mudancas.append('acesso: ' + ('reativado' if ativo else 'desativado'))
            usuario.ativo = ativo
        if not mudancas:
            flash('Nenhuma alteração para salvar.', 'info')
            return redirect(url_for('listar_usuarios'))

        registrar_auditoria(
            ACAO_USUARIO_EDITAR,
            entidade='usuario',
            entidade_id=usuario.id,
            descricao=f'{usuario.username}',
            detalhes='\n'.join(mudancas),
        )
        db.session.commit()
        app.logger.info('Usuário %s editado por %s: %s', usuario.username, session.get('username'), mudancas)
        flash(f'Usuário "{usuario.username}" atualizado.', 'success')
        return redirect(url_for('listar_usuarios'))

    return render_template(
        'usuario_editar.html',
        username=session.get('username'),
        usuario=usuario,
        eh_voce=eh_voce,
        unico_admin=_usuario_admin_ativo_unico(usuario),
    )


@app.route('/usuarios/<int:id>/redefinir_senha', methods=['POST'])
@admin_required
def redefinir_senha_usuario(id):
    usuario = db.session.get(Usuario, id)
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('listar_usuarios'))
    if usuario.id == session.get('usuario_id'):
        flash('Para trocar a sua própria senha, use "Meu perfil".', 'warning')
        return redirect(url_for('editar_usuario', id=id))

    senha = request.form.get('senha_provisoria', '')
    if len(senha) < 8:
        flash('A senha provisória deve ter no mínimo 8 caracteres.', 'danger')
        return redirect(url_for('editar_usuario', id=id))
    if senha != request.form.get('senha_provisoria_confirmacao', ''):
        flash('A confirmação não confere com a senha provisória.', 'danger')
        return redirect(url_for('editar_usuario', id=id))

    usuario.set_senha(senha)
    usuario.trocar_senha = True
    registrar_auditoria(
        ACAO_USUARIO_SENHA_REDEFINIDA,
        entidade='usuario',
        entidade_id=usuario.id,
        descricao=f'Senha provisória definida para {usuario.username}',
    )
    db.session.commit()
    app.logger.info('Senha de %s redefinida por %s', usuario.username, session.get('username'))
    flash(
        f'Senha provisória definida para "{usuario.username}". Informe-a pessoalmente; '
        'no próximo acesso será obrigatório criar uma senha nova.',
        'success',
    )
    return redirect(url_for('listar_usuarios'))


@app.route('/usuarios/<int:id>/excluir', methods=['POST'])
@admin_required
def excluir_usuario(id):
    usuario = db.session.get(Usuario, id)
    if not usuario:
        flash('Usuário não encontrado.', 'danger')
        return redirect(url_for('listar_usuarios'))

    if usuario.id == session.get('usuario_id'):
        flash('Você não pode excluir a si mesmo.', 'warning')
        return redirect(url_for('listar_usuarios'))

    # Impede remover o último administrador do sistema.
    if usuario.role == ROLE_ADMIN:
        if _usuario_admin_ativo_unico(usuario) or Usuario.query.filter_by(role=ROLE_ADMIN).count() <= 1:
            flash('Não é possível excluir o último administrador do sistema.', 'warning')
            return redirect(url_for('listar_usuarios'))

    try:
        nome_removido = usuario.username
        role_removido = usuario.role
        usuario_id_removido = usuario.id
        db.session.delete(usuario)
        registrar_auditoria(
            ACAO_USUARIO_EXCLUIR,
            entidade='usuario',
            entidade_id=usuario_id_removido,
            descricao=f'{nome_removido} (perfil: {role_removido})',
        )
        db.session.commit()
        app.logger.info('Usuário removido por %s: %s', session.get('username'), nome_removido)
        flash(f'Usuário "{nome_removido}" removido com sucesso.', 'success')
    except Exception:
        db.session.rollback()
        app.logger.exception('Erro ao excluir usuário id=%s', id)
        flash('Erro ao remover o usuário.', 'danger')

    return redirect(url_for('listar_usuarios'))


@app.route('/auditoria')
@admin_required
def auditoria():
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1

    filtros = {
        'usuario': request.args.get('usuario', '').strip(),
        'acao': request.args.get('acao', '').strip(),
        'entidade': request.args.get('entidade', '').strip(),
        'data_de': request.args.get('data_de', '').strip(),
        'data_ate': request.args.get('data_ate', '').strip(),
    }

    query = Auditoria.query

    if filtros['usuario']:
        query = query.filter(_contem_texto(Auditoria.usuario_username, filtros['usuario']))
    if filtros['acao']:
        query = query.filter(Auditoria.acao == filtros['acao'])
    if filtros['entidade']:
        query = query.filter(Auditoria.entidade == filtros['entidade'])
    if filtros['data_de']:
        try:
            d = datetime.strptime(filtros['data_de'], '%Y-%m-%d')
            query = query.filter(Auditoria.data_hora >= d)
        except ValueError:
            flash('Data inicial inválida.', 'warning')
    if filtros['data_ate']:
        try:
            d = datetime.strptime(filtros['data_ate'], '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(Auditoria.data_hora < d)
        except ValueError:
            flash('Data final inválida.', 'warning')

    paginacao = query.order_by(Auditoria.data_hora.desc()).paginate(
        page=page, per_page=50, error_out=False,
    )

    usuarios_distintos = [
        u[0] for u in db.session.query(Auditoria.usuario_username)
        .distinct().order_by(Auditoria.usuario_username).all()
    ]

    return render_template(
        'auditoria.html',
        username=session.get('username'),
        registros=paginacao.items,
        paginacao=paginacao,
        filtros=filtros,
        acoes=ACOES_ROTULOS,
        usuarios_distintos=usuarios_distintos,
    )
