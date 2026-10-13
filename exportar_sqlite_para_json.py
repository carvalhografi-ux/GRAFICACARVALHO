"""Converte um banco SQLite antigo para JSON de backup importável no sistema novo.

Uso: python exportar_sqlite_para_json.py banco_antigo.db backup_grafica.json
Nenhum dado é enviado pela internet. Usuários/senhas não fazem parte do backup.
"""
import base64
import json
import sqlite3
import sys
from pathlib import Path
from datetime import datetime


def exportar(arquivo_db, arquivo_json):
    source = Path(arquivo_db).resolve()
    target = Path(arquivo_json).resolve()
    if source == target:
        raise ValueError('A saída não pode sobrescrever o banco de origem.')
    if not source.is_file():
        raise FileNotFoundError('Banco SQLite não encontrado: ' + str(source))
    db = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        tables = {r['name'] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'orders' not in tables:
            raise ValueError('Este não é um banco da Gráfica Carvalho (orders ausente).')
        services = []
        for r in db.execute('SELECT * FROM orders ORDER BY created_at'):
            a = dict(r)
            services.append({
                'id':a['id'], 'code':a.get('code') or '',
                'client':a['client'], 'client_id':a.get('client_id') or '',
                'phone':a.get('phone') or '', 'service':a['service'],
                'qty':a.get('quantity') or 1,
                'entry':a['entry_date'], 'due':a['due_date'],
                'priority':a.get('priority') or 'Normal',
                'status':a.get('status') or 'Pendente',
                'total':a.get('total') or 0, 'received':a.get('received') or 0,
                'notes':a.get('notes') or ''
            })
        attachments = []
        if 'attachments' in tables:
            for r in db.execute('SELECT * FROM attachments ORDER BY created_at'):
                a = dict(r)
                attachments.append({
                    'id':a['id'], 'serviceId':a['order_id'],
                    'name':a['filename'], 'type':a['content_type'],
                    'size':a['size'], 'created':a['created_at'],
                    'base64':base64.b64encode(a['data']).decode('ascii')
                })
        clients = []
        if 'clients' in tables:
            clients = [dict(r) for r in db.execute('SELECT * FROM clients ORDER BY name')]
        last_num = 0
        if 'order_counter' in tables:
            r = db.execute('SELECT last_num FROM order_counter WHERE id=1').fetchone()
            if r: last_num = int(r[0])
        doc = {
            'app':'grafica-carvalho-python-offline',
            'exported':datetime.now().isoformat(timespec='seconds'),
            'services':services,
            'attachments':attachments,
            'clients':clients,
            'last_order_number':last_num
        }
        tmp = target.with_suffix(target.suffix + '.tmp')
        with tmp.open('w',encoding='utf-8') as f:
            json.dump(doc,f,ensure_ascii=False)
        tmp.replace(target)
        return len(services),len(attachments),len(clients)
    finally:
        db.close()


if __name__=='__main__':
    if len(sys.argv)!=3:
        print('Uso: python exportar_sqlite_para_json.py banco_antigo.db backup_grafica.json')
        sys.exit(2)
    pedidos,anexos,clientes=exportar(sys.argv[1],sys.argv[2])
    print(f'Backup privado criado: {pedidos} pedidos, {anexos} anexos, {clientes} clientes.')
    print('Guarde este JSON em local seguro. Nunca envie ao GitHub.')
