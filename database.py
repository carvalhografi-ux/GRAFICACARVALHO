"""Adaptador PostgreSQL/Neon para o aplicativo Gráfica Carvalho.
Mantém a interface de conexão usada pelo servidor antigo, mas toda consulta,
escrita, sessão de login, anexo e sequência usa PostgreSQL remoto.
"""
from __future__ import annotations
import os
import re
from contextlib import contextmanager
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row


class Row(dict):
    """Compatibilidade com sqlite3.Row: acesso por nome ou índice numérico."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def row_factory(cursor):
    build = dict_row(cursor)
    return lambda data: Row(build(data))


def connection_string():
    raw = os.environ.get('DATABASE_URL', '').strip()
    if not raw:
        raise RuntimeError('DATABASE_URL ausente: conecte um projeto Neon PostgreSQL ao Render.')
    scheme = urlsplit(raw).scheme
    if scheme not in ('postgresql', 'postgres'):
        raise RuntimeError('DATABASE_URL precisa ser uma URL PostgreSQL, não SQLite.')
    return raw


_named = re.compile(r'(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)')


def placeholders(sql: str, params=None) -> str:
    # Parâmetros continuam sendo transmitidos separadamente do SQL.
    if isinstance(params, dict):
        return _named.sub(lambda m: '%(' + m.group(1) + ')s', sql)
    return sql.replace('?', '%s')


class Connection:
    def __init__(self):
        self.raw = psycopg.connect(connection_string(), row_factory=row_factory,
                                   connect_timeout=20, autocommit=False, sslmode='require')

    def execute(self, sql, params=None):
        if params is None:
            return self.raw.execute(sql)
        return self.raw.execute(placeholders(sql, params), params)

    def executemany(self, sql, values):
        values = list(values)
        if not values:
            return
        with self.raw.cursor() as cursor:
            cursor.executemany(placeholders(sql, values[0]), values)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()

    def close(self):
        self.raw.close()


@contextmanager
def connect():
    db = Connection()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
