"""Criação de backups (SQLite + fotos + segredo de sessão) em arquivo ZIP.

Uso típico:
  - Interface web (admin) em ``main.admin_backup``.
  - Agendamento: ``python scripts/backup_cli.py`` (cron / Agendador de Tarefas).

Variáveis de ambiente opcionais:
  BACKUP_SYNC_DIR   Pasta extra para copiar o ZIP (ex.: pasta do Google Drive
                    no Windows, montada no WSL como ``/mnt/g/...`` ou
                    ``/mnt/c/Users/Nome/Google Drive/SGC-Backups``).
  BACKUP_KEEP_LOCAL Número de ZIPs a manter em ``data/backups/`` (padrão: 14).
  BACKUP_KEEP_SYNC  Número de ZIPs a manter em ``BACKUP_SYNC_DIR`` (padrão: 60).
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime

import pyzipper

_PREFIX = 'sgc_agmeal_backup_'
_NAME_RE = re.compile(r'^' + re.escape(_PREFIX) + r'\d{8}_\d{6}\.zip$')


def _sqlite_backup_to_file(src_db: str, dst_path: str, log: logging.Logger) -> None:
    """Cópia consistente do SQLite (evita copiar o arquivo .db com o app escrevendo)."""
    if not os.path.isfile(src_db):
        raise FileNotFoundError(f'Banco não encontrado: {src_db}')
    # Conexão normal (não mode=ro): um banco em WAL sem os arquivos -wal/-shm (servidor
    # parado) não abre em modo somente leitura.
    src = sqlite3.connect(src_db, timeout=60.0)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
        # A cópia vai sozinha para o ZIP: volta ao modo de journal tradicional para abrir
        # sem arquivos auxiliares (o app religa o WAL ao usar o banco restaurado).
        dst.execute('PRAGMA journal_mode=DELETE')
    finally:
        dst.close()
        src.close()


def zip_criptografado(zip_path: str) -> bool:
    """True se algum arquivo do ZIP estiver protegido por senha."""
    with zipfile.ZipFile(zip_path) as zf:
        return any(info.flag_bits & 0x1 for info in zf.infolist())


def abrir_zip(zip_path: str, senha: str | None = None):
    """Abre ZIP comum ou AES (WinZip). A senha só é usada se o ZIP for protegido."""
    zf = pyzipper.AESZipFile(zip_path)
    if senha:
        zf.setpassword(senha.encode('utf-8'))
    return zf


def verificar_backup_zip(zip_path: str, senha: str | None = None) -> None:
    """Confere se o ZIP está íntegro e se o banco dentro dele abre sem corrupção.

    Levanta ValueError com a causa se algo estiver errado."""
    try:
        with abrir_zip(zip_path, senha) as zf:
            ruim = zf.testzip()
            if ruim:
                raise ValueError(f'Arquivo corrompido dentro do ZIP: {ruim}')
            if 'data/sgc.db' not in zf.namelist():
                return  # backup de instalação sem banco (caso raro, já sinalizado no ZIP)
            with tempfile.TemporaryDirectory() as tmp:
                db_tmp = zf.extract('data/sgc.db', tmp)
                conn = sqlite3.connect(f'file:{db_tmp}?mode=ro', uri=True)
                try:
                    resultado = conn.execute('PRAGMA quick_check').fetchone()[0]
                    tabelas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                finally:
                    conn.close()
    except (zipfile.BadZipFile, pyzipper.BadZipFile) as exc:
        raise ValueError(f'ZIP inválido: {exc}') from exc
    except RuntimeError as exc:  # senha ausente ou errada
        raise ValueError(f'Não foi possível abrir o backup protegido: {exc}') from exc
    except sqlite3.DatabaseError as exc:
        raise ValueError(f'Banco do backup não abre: {exc}') from exc
    if resultado != 'ok':
        raise ValueError(f'Verificação do banco falhou: {resultado}')
    if not {'usuarios', 'associados'} <= tabelas:
        raise ValueError('Banco do backup não contém as tabelas esperadas.')


def _leia_me_txt() -> str:
    return """SGC-AGMEAL — backup
====================

Conteúdo deste ZIP:
  data/sgc.db          — cópia consistente do banco SQLite
  fotos/               — imagens de perfil dos associados
  data/.flask_secret   — chave de sessão (se existia no momento do backup)

Restauração (resumo):
  1. Administradores podem usar a página «Restaurar backup» na aplicação (com
     confirmação explícita por texto). Reinicie o servidor com vários workers
     após concluir.
  2. Restauração manual: pare o servidor (Gunicorn / Docker).
  3. Extraia este ZIP sobre a pasta do projeto (substituindo arquivos).
  4. Confirme permissões da pasta data/ e app/static/uploads/fotos/.
  5. Inicie o servidor novamente.

Em caso de dúvida, peça ajuda a quem instalou o sistema.
"""


def _listar_zips_para_rotação(pasta: str) -> list[tuple[float, str]]:
    if not os.path.isdir(pasta):
        return []
    out = []
    for nome in os.listdir(pasta):
        caminho = os.path.join(pasta, nome)
        if os.path.isfile(caminho) and _NAME_RE.match(nome):
            out.append((os.path.getmtime(caminho), caminho))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def _rotacionar(pasta: str, manter: int, log: logging.Logger) -> None:
    if manter < 1:
        return
    arquivos = _listar_zips_para_rotação(pasta)
    for _, caminho in arquivos[manter:]:
        try:
            os.remove(caminho)
            log.info('Backup antigo removido: %s', caminho)
        except OSError as exc:
            log.warning('Não foi possível remover backup antigo %s: %s', caminho, exc)


def criar_backup_zip(
    *,
    data_dir: str,
    upload_folder: str,
    backups_dir: str,
    sync_dir: str | None,
    keep_local: int,
    keep_sync: int,
    log: logging.Logger,
    senha: str | None = None,
) -> dict:
    """
    Com `senha`, o ZIP é criptografado com AES-256 (padrão WinZip: abre no 7-Zip).

    Retorna dict com:
      zip_path, zip_filename, size_bytes, sync_path (ou None), had_db, had_secret, criptografado
    """
    os.makedirs(backups_dir, mode=0o700, exist_ok=True)

    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    zip_filename = f'{_PREFIX}{ts}.zip'
    zip_path = os.path.join(backups_dir, zip_filename)

    db_src = os.path.join(data_dir, 'sgc.db')
    secret_src = os.path.join(data_dir, '.flask_secret')

    had_db = os.path.isfile(db_src)
    had_secret = os.path.isfile(secret_src)

    tmp_db: str | None = None
    try:
        if senha:
            zf_ctx = pyzipper.AESZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED,
                                         encryption=pyzipper.WZ_AES)
            zf_ctx.setpassword(senha.encode('utf-8'))
        else:
            zf_ctx = zipfile.ZipFile(zip_path, 'w', compression=zipfile.ZIP_DEFLATED)
        with zf_ctx as zf:
            if had_db:
                with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as tmp:
                    tmp_db = tmp.name
                try:
                    _sqlite_backup_to_file(db_src, tmp_db, log)
                    zf.write(tmp_db, arcname='data/sgc.db')
                finally:
                    if tmp_db and os.path.isfile(tmp_db):
                        os.unlink(tmp_db)
                        tmp_db = None
            else:
                zf.writestr(
                    'data/LEIA-ME_SEM_BANCO.txt',
                    'Não havia arquivo data/sgc.db no momento deste backup.\n',
                )

            if had_secret:
                zf.write(secret_src, arcname='data/.flask_secret')

            zf.writestr('LEIA-ME.txt', _leia_me_txt())

            upload_real = os.path.realpath(upload_folder)
            if os.path.isdir(upload_real):
                for root, _, files in os.walk(upload_real):
                    for fn in files:
                        abs_path = os.path.join(root, fn)
                        rel = os.path.relpath(abs_path, upload_real)
                        if rel.startswith('..'):
                            continue
                        arc = os.path.join('fotos', rel).replace('\\', '/')
                        zf.write(abs_path, arcname=arc)

        # Um backup que não restaura não é backup: verifica antes de contar como válido.
        try:
            verificar_backup_zip(zip_path, senha)
        except ValueError:
            os.remove(zip_path)
            raise
        size_bytes = os.path.getsize(zip_path)
    finally:
        if tmp_db and os.path.isfile(tmp_db):
            try:
                os.unlink(tmp_db)
            except OSError:
                pass

    _rotacionar(backups_dir, keep_local, log)

    sync_path = None
    if sync_dir:
        sync_dir = sync_dir.strip()
        if sync_dir:
            try:
                os.makedirs(sync_dir, mode=0o700, exist_ok=True)
                dest = os.path.join(sync_dir, zip_filename)
                shutil.copy2(zip_path, dest)
                sync_path = dest
                log.info('Backup copiado para pasta de sincronização: %s', dest)
                _rotacionar(sync_dir, keep_sync, log)
            except OSError as exc:
                log.error('Falha ao copiar backup para BACKUP_SYNC_DIR=%s: %s', sync_dir, exc)
                raise

    return {
        'zip_path': zip_path,
        'zip_filename': zip_filename,
        'size_bytes': size_bytes,
        'sync_path': sync_path,
        'had_db': had_db,
        'had_secret': had_secret,
        'criptografado': bool(senha),
    }


def listar_backups_locais(backups_dir: str, limite: int = 20) -> list[dict]:
    """Lista ZIPs de backup mais recentes primeiro."""
    arquivos = _listar_zips_para_rotação(backups_dir)
    out = []
    for mtime, caminho in arquivos[:limite]:
        st = os.stat(caminho)
        out.append({
            'nome': os.path.basename(caminho),
            'caminho': caminho,
            'bytes': st.st_size,
            'mtime': mtime,
        })
    return out
