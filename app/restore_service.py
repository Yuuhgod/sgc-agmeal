"""Análise e aplicação de restauração a partir de ZIP gerado pelo backup SGC-AGMEAL."""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile

MARKER_NAME = '.sgc_restore_marker.json'


def _norm_member(name: str) -> str:
    return name.replace('\\', '/').strip('/')


def membro_zip_permitido(name: str) -> bool:
    n = _norm_member(name)
    if not n or n.startswith('..') or '/..' in n:
        return False
    if n in ('LEIA-ME.txt', 'data/LEIA-ME_SEM_BANCO.txt'):
        return True
    if n in ('data/sgc.db', 'data/.flask_secret'):
        return True
    if n.startswith('fotos/'):
        return True
    return False


def extrair_zip_seguro(zip_path: str, dest_dir: str, log: logging.Logger) -> dict:
    """Extrai apenas membros permitidos para dest_dir. Retorna resumo."""
    fotos = 0
    tem_db = False
    tem_secret = False
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if not membro_zip_permitido(info.filename):
                raise ValueError(f'Ficheiro não permitido no ZIP: {info.filename!r}')
            name = _norm_member(info.filename)
            if name == 'data/sgc.db':
                tem_db = True
            if name == 'data/.flask_secret':
                tem_secret = True
            if name.startswith('fotos/'):
                fotos += 1
        if not tem_db:
            raise ValueError('O ZIP não contém data/sgc.db (backup inválido ou incompleto).')

    os.makedirs(dest_dir, mode=0o700, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _norm_member(info.filename)
            target = os.path.join(dest_dir, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, 'wb') as out:
                shutil.copyfileobj(src, out)

    return {'fotos': fotos, 'tem_secret': tem_secret}


def escrever_marcador(extract_root: str, usuario_id: int) -> None:
    path = os.path.join(extract_root, MARKER_NAME)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump({'usuario_id': usuario_id}, fh)


def ler_marcador_validar(extract_root: str, usuario_id: int) -> bool:
    path = os.path.join(extract_root, MARKER_NAME)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding='utf-8') as fh:
            data = json.load(fh)
        return int(data.get('usuario_id', -1)) == int(usuario_id)
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def aplicar_restauracao(
    *,
    extract_root: str,
    data_dir: str,
    upload_folder: str,
    log: logging.Logger,
) -> None:
    """Substitui sgc.db, fotos e opcionalmente .flask_secret. Fecha ligações SQLAlchemy antes."""
    src_db = os.path.join(extract_root, 'data', 'sgc.db')
    if not os.path.isfile(src_db):
        raise FileNotFoundError('data/sgc.db em falta na extração.')

    dst_db = os.path.join(data_dir, 'sgc.db')
    dst_new = dst_db + '.novo'

    shutil.copy2(src_db, dst_new)
    os.replace(dst_new, dst_db)
    log.info('Banco substituído: %s', dst_db)

    src_fotos = os.path.join(extract_root, 'fotos')
    if os.path.isdir(src_fotos):
        upload_real = os.path.realpath(upload_folder)
        if os.path.isdir(upload_real):
            for nome in os.listdir(upload_real):
                p = os.path.join(upload_real, nome)
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError as exc:
                    log.warning('Falha ao remover foto antiga %s: %s', p, exc)
        for root, _, files in os.walk(src_fotos):
            for fn in files:
                abs_src = os.path.join(root, fn)
                rel = os.path.relpath(abs_src, src_fotos)
                if rel.startswith('..'):
                    continue
                dst = os.path.join(upload_real, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(abs_src, dst)
        log.info('Fotos restauradas a partir do ZIP.')

    src_sec = os.path.join(extract_root, 'data', '.flask_secret')
    if os.path.isfile(src_sec):
        dst_sec = os.path.join(data_dir, '.flask_secret')
        dst_new = dst_sec + '.novo'
        try:
            shutil.copy2(src_sec, dst_new)
            try:
                os.chmod(dst_new, 0o600)
            except OSError:
                pass
            os.replace(dst_new, dst_sec)
            log.info('.flask_secret restaurado.')
        except OSError as exc:
            log.warning(
                'Não foi possível substituir .flask_secret (permissões no destino?). '
                'Mantenha o ficheiro atual ou ajuste permissões e copie manualmente do ZIP. Erro: %s',
                exc,
            )
            try:
                if os.path.isfile(dst_new):
                    os.unlink(dst_new)
            except OSError:
                pass
