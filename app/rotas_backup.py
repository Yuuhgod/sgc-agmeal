"""Backup e restauração pela interface (admin)."""

import os
import signal
import tempfile
import uuid
from datetime import datetime

from flask import (
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from werkzeug.utils import secure_filename

import backup_agendador
from backup_service import criar_backup_zip, listar_backups_locais
from database import (
    ACAO_SISTEMA_BACKUP,
    ACAO_SISTEMA_BACKUP_SENHA,
    ACAO_SISTEMA_RESTORE,
    Usuario,
    db,
)
from nucleo import (
    BACKUP_SENHA_MIN,
    RESTORE_CONFIRM_PHRASE,
    UPLOAD_FOLDER,
    _backup_keep_local,
    _backup_keep_sync,
    _backup_sync_dir,
    _garantir_schema,
    _gravar_senha_backup,
    _senha_backup,
    _situacao_backup,
    admin_required,
    app,
    backups_dir,
    data_dir,
    limiter,
    registrar_auditoria,
    restore_pending_dir,
)
from restore_service import (
    SenhaBackupNecessaria,
    aplicar_restauracao,
    extrair_zip_seguro,
)


@app.route('/admin/backup', methods=['GET'])
@admin_required
def admin_backup():
    recentes = listar_backups_locais(backups_dir)
    sync_dir = _backup_sync_dir()
    return render_template(
        'admin_backup.html',
        username=session.get('username'),
        sync_dir=sync_dir,
        recentes=recentes,
        situacao=_situacao_backup(),
        keep_local=_backup_keep_local(),
        keep_sync=_backup_keep_sync(),
    )


@app.route('/admin/backup/senha', methods=['POST'])
@admin_required
def admin_backup_senha():
    """Define ou troca a senha dos backups (exige a senha de login do admin)."""
    admin = db.session.get(Usuario, session['usuario_id'])
    if not admin.check_senha(request.form.get('senha_login', '')):
        flash('Sua senha de login está incorreta. Nada foi alterado.', 'danger')
        return redirect(url_for('admin_backup'))
    if os.environ.get('BACKUP_SENHA', '').strip():
        flash('A senha dos backups está definida no ambiente do servidor (BACKUP_SENHA) e não pode ser trocada por aqui.', 'warning')
        return redirect(url_for('admin_backup'))

    nova = request.form.get('nova_senha_backup', '')
    if len(nova) < BACKUP_SENHA_MIN:
        flash(f'A senha dos backups deve ter pelo menos {BACKUP_SENHA_MIN} caracteres.', 'danger')
        return redirect(url_for('admin_backup'))
    if nova != request.form.get('confirmacao_senha_backup', ''):
        flash('A confirmação não confere com a nova senha dos backups.', 'danger')
        return redirect(url_for('admin_backup'))

    tinha = bool(_senha_backup())
    _gravar_senha_backup(nova)
    registrar_auditoria(
        ACAO_SISTEMA_BACKUP_SENHA,
        entidade='backup',
        descricao='Senha dos backups ' + ('alterada' if tinha else 'definida'),
        commit=True,
    )
    flash(
        'Senha dos backups salva. Os próximos backups serão criptografados. ANOTE a senha em local seguro: '
        'sem ela não é possível restaurar os backups em outro computador.',
        'success',
    )
    return redirect(url_for('admin_backup'))


@app.route('/admin/backup/gerar', methods=['POST'])
@admin_required
@limiter.limit('12 per hour')
def admin_backup_gerar():
    copiar = request.form.get('copiar_drive') == '1'
    sync_dir = _backup_sync_dir() if copiar else None
    if copiar and not sync_dir:
        flash(
            'Para copiar automaticamente para a nuvem, defina BACKUP_SYNC_DIR no servidor '
            '(caminho da pasta sincronizada pelo Google Drive ou OneDrive).',
            'warning',
        )
        return redirect(url_for('admin_backup'))

    try:
        info = criar_backup_zip(
            data_dir=data_dir,
            upload_folder=UPLOAD_FOLDER,
            backups_dir=backups_dir,
            sync_dir=sync_dir,
            keep_local=_backup_keep_local(),
            keep_sync=_backup_keep_sync(),
            log=current_app.logger,
            senha=_senha_backup(),
        )
    except Exception as exc:
        current_app.logger.exception('Falha ao gerar backup')
        agora_iso = datetime.now().isoformat(timespec='seconds')
        backup_agendador.gravar_status(backups_dir, ultima_tentativa=agora_iso, ultima_falha=agora_iso, erro=str(exc)[:300])
        flash('Não foi possível gerar o backup. Verifique permissões de pasta e o log do servidor.', 'danger')
        return redirect(url_for('admin_backup'))

    agora_iso = datetime.now().isoformat(timespec='seconds')
    backup_agendador.gravar_status(
        backups_dir, ultima_tentativa=agora_iso, ultimo_sucesso=agora_iso, erro=None,
        arquivo=info['zip_filename'], copia_nuvem=bool(info['sync_path']),
    )
    detalhes = (
        f"arquivo={info['zip_filename']}\n"
        f"tamanho_bytes={info['size_bytes']}\n"
        f"copia_nuvem={'sim' if info['sync_path'] else 'não'}"
    )
    registrar_auditoria(
        ACAO_SISTEMA_BACKUP,
        entidade='backup',
        descricao='Backup ZIP gerado',
        detalhes=detalhes,
        commit=True,
    )

    return send_file(
        info['zip_path'],
        mimetype='application/zip',
        as_attachment=True,
        download_name=info['zip_filename'],
    )


@app.route('/admin/restore', methods=['GET', 'POST'])
@admin_required
@limiter.limit('6 per hour', methods=['POST'])
def admin_restore():
    if request.method == 'GET':
        return render_template(
            'admin_restore.html',
            username=session.get('username'),
            confirm_phrase=RESTORE_CONFIRM_PHRASE,
        )

    if request.form.get('confirmar_texto', '').strip() != RESTORE_CONFIRM_PHRASE:
        flash(
            f'Digite exatamente a palavra {RESTORE_CONFIRM_PHRASE!r} no campo de confirmação.',
            'danger',
        )
        return redirect(url_for('admin_restore'))

    if request.form.get('confirmar_consciencia') != '1':
        flash('Marque a caixa confirmando que entende que os dados atuais serão substituídos.', 'warning')
        return redirect(url_for('admin_restore'))

    upload = request.files.get('arquivo')
    if not upload or not upload.filename:
        flash('Selecione o arquivo ZIP de backup.', 'warning')
        return redirect(url_for('admin_restore'))

    nome_original = secure_filename(upload.filename) or 'backup.zip'
    if not nome_original.lower().endswith('.zip'):
        flash('O arquivo deve ser um ZIP gerado pelo backup deste sistema.', 'danger')
        return redirect(url_for('admin_restore'))

    zip_path = os.path.join(restore_pending_dir, f'upload_{uuid.uuid4().hex}.zip')
    try:
        upload.save(zip_path)
    except OSError:
        current_app.logger.exception('Falha ao gravar ZIP de restauração')
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except OSError:
            pass
        flash('Não foi possível guardar o arquivo enviado. Verifique espaço em disco e permissões.', 'danger')
        return redirect(url_for('admin_restore'))

    auditoria_uid = session.get('usuario_id')
    auditoria_user = session.get('username') or '(anônimo)'

    try:
        with tempfile.TemporaryDirectory(dir=restore_pending_dir) as extract_root:
            extrair_zip_seguro(
                zip_path, extract_root, current_app.logger,
                senhas=(request.form.get('senha_backup', ''), _senha_backup()),
            )

            try:
                criar_backup_zip(
                    data_dir=data_dir,
                    upload_folder=UPLOAD_FOLDER,
                    backups_dir=backups_dir,
                    sync_dir=None,
                    keep_local=_backup_keep_local(),
                    keep_sync=_backup_keep_sync(),
                    log=current_app.logger,
                    senha=_senha_backup(),
                )
            except Exception:
                current_app.logger.exception('Falha no backup de segurança antes da restauração')
                flash(
                    'Não foi possível criar um backup de segurança dos dados atuais. '
                    'A restauração foi cancelada; nada foi alterado.',
                    'danger',
                )
                return redirect(url_for('admin_restore'))

            db.session.remove()
            if db.engine is not None:
                db.engine.dispose()

            try:
                aplicar_restauracao(
                    extract_root=extract_root,
                    data_dir=data_dir,
                    upload_folder=UPLOAD_FOLDER,
                    log=current_app.logger,
                )
            except Exception:
                current_app.logger.exception('Falha ao aplicar restauração')
                flash(
                    'Ocorreu um erro ao aplicar o backup. Os dados podem estar inconsistentes; '
                    'use o ZIP de segurança mais recente em data/backups/ ou restaure manualmente '
                    '(veja LEIA-ME.txt dentro do ZIP).',
                    'danger',
                )
                return redirect(url_for('admin_restore'))

            db.session.remove()
            if db.engine is not None:
                db.engine.dispose()

            # Um backup antigo pode não ter as colunas acrescentadas depois.
            _garantir_schema()

            registrar_auditoria(
                ACAO_SISTEMA_RESTORE,
                entidade='restore',
                descricao='Restauração aplicada a partir de ZIP enviado na interface',
                detalhes=f'arquivo_enviado={nome_original}',
                usuario_id=auditoria_uid,
                usuario_username=auditoria_user,
                commit=True,
            )

    except SenhaBackupNecessaria as exc:
        flash(str(exc), 'warning')
        return redirect(url_for('admin_restore'))
    except ValueError as exc:
        current_app.logger.warning('ZIP de restauração rejeitado: %s', exc)
        flash(str(exc), 'danger')
        return redirect(url_for('admin_restore'))
    except Exception:
        current_app.logger.exception('Erro inesperado na restauração')
        flash('Não foi possível processar o ZIP. Verifique se é um backup válido deste sistema.', 'danger')
        return redirect(url_for('admin_restore'))
    finally:
        try:
            if os.path.isfile(zip_path):
                os.remove(zip_path)
        except OSError:
            current_app.logger.warning('Não foi possível remover ficheiro temporário: %s', zip_path)

    session.clear()
    if _recarregar_workers_gunicorn():
        flash('Restauração concluída. O servidor foi recarregado com os dados restaurados; faça login novamente.', 'success')
    else:
        flash(
            'Restauração concluída. Faça login novamente. '
            'Se o servidor usar vários processos (Gunicorn/Docker), reinicie o serviço para todos carregarem o novo banco.',
            'success',
        )
    return redirect(url_for('login'))


def _recarregar_workers_gunicorn():
    """Sob o Gunicorn, pede ao processo mestre (SIGHUP) que troque todos os workers: os outros
    processos ainda estariam com o banco antigo aberto. A troca é graciosa (esta requisição termina)."""
    if 'gunicorn' not in (request.environ.get('SERVER_SOFTWARE') or '').lower():
        return False
    try:
        os.kill(os.getppid(), signal.SIGHUP)
        app.logger.info('Restauração: SIGHUP enviado ao Gunicorn (pid %s) para recarregar os workers.', os.getppid())
        return True
    except OSError:
        app.logger.warning('Não foi possível pedir ao Gunicorn que recarregue os workers.', exc_info=True)
        return False
