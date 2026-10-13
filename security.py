"""Autenticacao/controle de acesso Gráfica Carvalho (somente biblioteca padrão).
Cookies de sessão opacos com SHA-256 no servidor; senhas com scrypt e sal único;
proteção CSRF em mutações autenticadas; limitação persistente de login.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path

SESSION_SECONDS = 8 * 60 * 60
COOKIE = 'gc_session'
DATABASE = None

class AuthError(Exception):
    def __init__(self, status, message):
        self.status=status
        self.message=message
        super().__init__(message)

def setup(db_path):
    global DATABASE
    DATABASE = Path(db_path)
    with db() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS app_users (
            id TEXT PRIMARY KEY, email TEXT NOT NULL COLLATE NOCASE UNIQUE,
            name TEXT NOT NULL, password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','staff','client')),
            client_id TEXT, active INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS app_sessions (
            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
            csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL
        )''')
        c.execute('CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON app_sessions(expires_at)')
        c.execute('''CREATE TABLE IF NOT EXISTS login_failures (
            login_key TEXT PRIMARY KEY, failures INTEGER NOT NULL, first_at INTEGER NOT NULL, locked_until INTEGER NOT NULL DEFAULT 0
        )''')
        count=c.execute("SELECT COUNT(*) FROM app_users WHERE role='admin'").fetchone()[0]
        if count==0:
            email=os.environ.get('GRAFICA_ADMIN_EMAIL','').strip().lower()
            pwd=os.environ.get('GRAFICA_ADMIN_PASSWORD','')
            if not email or len(pwd)<12:
                raise RuntimeError('Primeira inicialização: defina GRAFICA_ADMIN_EMAIL e GRAFICA_ADMIN_PASSWORD (minimo 12 caracteres) nas variáveis do servidor Render.')
            user_id=uuid.uuid4().hex
            c.execute('INSERT INTO app_users (id,email,name,password_hash,role,client_id,active,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                  (user_id,email,'Administrador',password_hash(pwd),'admin',None,1,int(time.time()),int(time.time())))
        c.execute('DELETE FROM app_sessions WHERE expires_at<?',(int(time.time()),))

@contextmanager
def db():
    c=sqlite3.connect(str(DATABASE),timeout=20)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=20000')
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

def password_hash(value):
    salt=secrets.token_bytes(16)
    hashed=hashlib.scrypt(value.encode('utf-8'),salt=salt,n=2**14,r=8,p=1,dklen=32)
    return 'scrypt-v1$'+salt.hex()+'$'+hashed.hex()

def verify_password(value,stored):
    try:
        algo,salt,hashval=stored.split('$')
        if algo!='scrypt-v1':return False
        derived=hashlib.scrypt(value.encode('utf-8'),salt=bytes.fromhex(salt),n=2**14,r=8,p=1,dklen=32)
        return hmac.compare_digest(derived,bytes.fromhex(hashval))
    except (ValueError,TypeError):return False

def public_user(row):
    return {key:row[key] for key in ('id','email','name','role','client_id','active') if key in row.keys()}

def cookie_value(headers):
    try:
        ck=SimpleCookie()
        ck.load(headers.get('Cookie',''))
        return ck[COOKIE].value if COOKIE in ck else None
    except Exception:return None

def token_hash(token):return hashlib.sha256(token.encode('utf-8')).hexdigest()

def bearer_value(headers):
    authorization=headers.get('Authorization','')
    if not authorization.startswith('Bearer '):return None
    token=authorization[7:].strip()
    return token if 20 <= len(token) <= 180 else None

def session(headers):
    token=bearer_value(headers) or cookie_value(headers)
    if not token or len(token)>180:return None
    with db() as c:
        row=c.execute('''SELECT u.*,s.csrf_token FROM app_sessions s JOIN app_users u ON u.id=s.user_id
             WHERE s.token_hash=? AND s.expires_at>? AND u.active=1''',(token_hash(token),int(time.time()))).fetchone()
    return dict(row) if row else None

def require_role(user,roles):
    if not user:raise AuthError(401,'Faça login para continuar.')
    if user['role'] not in roles:raise AuthError(403,'Você não tem permissão para acessar este recurso.')

def csrf(user,headers):
    if not user or bearer_value(headers):return
    sent=headers.get('X-CSRF-Token','')
    if not sent or not hmac.compare_digest(sent,user['csrf_token']):
        raise AuthError(403,'Verificação de segurança expirada. Recarregue a página.')

def secure_cookie(cookie_token, secure=True, remove=False):
    # Cookie de sessão não pode ser acessado por JavaScript.
    value=f'{COOKIE}={cookie_token if not remove else ""}; Path=/; HttpOnly; SameSite=Lax'
    if secure:value+='; Secure'
    value+=('; Max-Age=0' if remove else f'; Max-Age={SESSION_SECONDS}')
    return value

def login(email,password):
    name=str(email or '').strip().lower()[:160]
    if not name or not isinstance(password,str):raise AuthError(401,'Usuário ou senha incorretos.')
    now=int(time.time());key=hashlib.sha256(name.encode()).hexdigest()
    with db() as c:
        failure=c.execute('SELECT * FROM login_failures WHERE login_key=?',(key,)).fetchone()
        if failure and failure['locked_until']>now:
            raise AuthError(429,'Muitas tentativas. Aguarde 15 minutos antes de tentar novamente.')
        user=c.execute('SELECT * FROM app_users WHERE email=?',(name,)).fetchone()
        # Calcula hash falso também para reduzir diferenças de tempo na existência do usuário.
        dummy='scrypt-v1$00112233445566778899aabbccddeeff$4e65903a8229dca42a3347dfb4b0af5c8d9a441338d38da22f76374158a2398d'
        ok=verify_password(password,user['password_hash'] if user else dummy)
        if not user or not user['active'] or not ok:
            errors=(int(failure['failures'])+1 if failure and now-failure['first_at']<900 else 1)
            first=int(failure['first_at']) if failure and now-failure['first_at']<900 else now
            until=now+900 if errors>=5 else 0
            c.execute('INSERT INTO login_failures(login_key,failures,first_at,locked_until) VALUES(?,?,?,?) ON CONFLICT(login_key) DO UPDATE SET failures=excluded.failures,first_at=excluded.first_at,locked_until=excluded.locked_until',(key,errors,first,until))
            failed=True
        else:
            failed=False
            c.execute('DELETE FROM login_failures WHERE login_key=?',(key,))
            token=secrets.token_urlsafe(36);csrf_value=secrets.token_urlsafe(32)
            c.execute('INSERT INTO app_sessions VALUES (?,?,?,?,?)',(token_hash(token),user['id'],csrf_value,now+SESSION_SECONDS,now))
            result=public_user(user)
            result['csrf']=csrf_value
    if failed:raise AuthError(401,'Usuário ou senha incorretos.')
    return token,result

def logout(headers):
    token=bearer_value(headers) or cookie_value(headers)
    if token:
        with db() as c:c.execute('DELETE FROM app_sessions WHERE token_hash=?',(token_hash(token),))

def list_users():
    with db() as c:
        return [public_user(u) for u in c.execute('SELECT * FROM app_users ORDER BY CASE role WHEN "admin" THEN 0 WHEN "staff" THEN 1 ELSE 2 END,name COLLATE NOCASE')]

def create_user(payload):
    if not isinstance(payload,dict):raise AuthError(422,'Dados inválidos.')
    email=str(payload.get('email') or '').strip().lower()
    name=str(payload.get('name') or '').strip()
    role=str(payload.get('role') or '')
    pwd=payload.get('password') or ''
    cid=str(payload.get('client_id') or '').strip() or None
    if not (3<=len(name)<=120 and len(email)<=160 and '@' in email and '.' in email.split('@')[-1]):raise AuthError(422,'Informe nome e e-mail válidos.')
    if role not in ('admin','staff','client'):raise AuthError(422,'Tipo de conta inválido.')
    if not isinstance(pwd,str) or len(pwd)<12 or len(pwd)>256:raise AuthError(422,'Use uma senha de 12 a 256 caracteres.')
    if role=='client' and not cid:raise AuthError(422,'Selecione o cadastro do cliente.')
    if role!='client':cid=None
    with db() as c:
        if cid and not c.execute('SELECT 1 FROM clients WHERE id=?',(cid,)).fetchone():raise AuthError(422,'Cliente não encontrado.')
        try:
            uid=uuid.uuid4().hex;t=int(time.time())
            c.execute('INSERT INTO app_users VALUES (?,?,?,?,?,?,?,?,?)',(uid,email,name,password_hash(pwd),role,cid,1,t,t))
        except sqlite3.IntegrityError:raise AuthError(409,'Este e-mail já está cadastrado.')
        return public_user(c.execute('SELECT * FROM app_users WHERE id=?',(uid,)).fetchone())

def update_user(uid,payload,actor_id):
    if not isinstance(payload,dict):raise AuthError(422,'Dados inválidos.')
    with db() as c:
        target=c.execute('SELECT * FROM app_users WHERE id=?',(uid,)).fetchone()
        if not target:raise AuthError(404,'Conta não encontrada.')
        action=payload.get('action')
        if action=='reset_password':
            pwd=payload.get('password')
            if not isinstance(pwd,str) or not 12<=len(pwd)<=256:raise AuthError(422,'Senha deve ter pelo menos 12 caracteres.')
            c.execute('UPDATE app_users SET password_hash=?,updated_at=? WHERE id=?',(password_hash(pwd),int(time.time()),uid))
            c.execute('DELETE FROM app_sessions WHERE user_id=?',(uid,))
        elif action=='active':
            state=payload.get('active')
            if not isinstance(state,bool):raise AuthError(422,'Estado inválido.')
            if uid==actor_id and not state:raise AuthError(422,'Você não pode bloquear sua própria conta.')
            if target['role']=='admin' and not state:
                if c.execute("SELECT COUNT(*) FROM app_users WHERE role='admin' AND active=1").fetchone()[0]<=1:
                    raise AuthError(422,'A última conta de administrador não pode ser bloqueada.')
            c.execute('UPDATE app_users SET active=?,updated_at=? WHERE id=?',(int(state),int(time.time()),uid))
            if not state:c.execute('DELETE FROM app_sessions WHERE user_id=?',(uid,))
        else:raise AuthError(422,'Operação não reconhecida.')
        return public_user(c.execute('SELECT * FROM app_users WHERE id=?',(uid,)).fetchone())

def change_password(uid,old,new):
    if not isinstance(new,str) or not 12<=len(new)<=256:raise AuthError(422,'A senha nova deve ter pelo menos 12 caracteres.')
    with db() as c:
        user=c.execute('SELECT * FROM app_users WHERE id=?',(uid,)).fetchone()
        if not user or not verify_password(str(old or ''),user['password_hash']):raise AuthError(403,'Senha atual incorreta.')
        c.execute('UPDATE app_users SET password_hash=?,updated_at=? WHERE id=?',(password_hash(new),int(time.time()),uid))
        c.execute('DELETE FROM app_sessions WHERE user_id=?',(uid,))
