# Gráfica Carvalho — GRATUITO: Wix + Render Free + Neon Free

O sistema continua em Python e mantém pedidos, clientes, usuários, pagamentos, anexos e impressão de OS A4/A5. Em vez de SQLite, **todos os dados agora ficam no Neon PostgreSQL**, incluindo sessões de login e anexos. Nenhum dado é gravado em um disco persistente do Render.

## Instalação (sem colocar senhas no GitHub ou neste chat)

1. Crie um projeto **Free** em https://console.neon.tech. Copie sua *PostgreSQL connection string* com TLS, preferencialmente a opção pooled (host contendo `-pooler`). **A URL é uma credencial sigilosa**.
2. Crie um Render **Web Service** usando este repositório e o plano **Free**. O `render.yaml` está configurado para free e **não cria disco pago**. Você pode usá-lo na opção **New → Blueprint**.
3. No painel privado **Render → Environment**, configure:
   - `DATABASE_URL`: URL do banco Neon (não envie ao GitHub ou ao Wix).
   - `GRAFICA_ADMIN_EMAIL`: e-mail inicial do administrador.
   - `GRAFICA_ADMIN_PASSWORD`: uma senha forte e inédita de 12 a 256 caracteres.
   - `GRAFICA_WIX_ORIGIN`: já definida no `render.yaml` como `https://headless-fynqkyfsoyi-carvalhografi-140d.wix-site-host.com`.
   - `GRAFICA_SECURE_COOKIES`: já definida como `1`.
4. Aguarde o deploy e teste `https://ENDERECO-REAL.onrender.com/healthz`; deve retornar `{"ok":true}`.
5. No site Wix publicado, altere o `config.js` para apontar à URL HTTPS real da API Render, sem barra final. **Jamais coloque `DATABASE_URL` na interface pública**.
6. Entre como administrador pelo Wix e crie contas de funcionários e clientes. Cada conta cliente só visualiza os próprios pedidos.
7. Para importar histórico, use a opção **Backup JSON** no sistema antigo e **Restaurar backup** no novo. Se tiver apenas o banco `grafica_carvalho.db`, use `python exportar_sqlite_para_json.py CAMINHO_DO_BANCO CAMINHO_DO_BACKUP.json` no seu computador. O backup é privado; nunca o envie ao GitHub.
8. Confira pedidos, anexos, clientes, PDFs e numeração automática e faça backups periódicos em local seguro. O backup JSON **não inclui senhas de usuários**; recrie ou redefina os logins.

## Restrições do uso gratuito

- **Render Free** suspende a instância após cerca de 15 minutos sem tráfego. A primeira abertura pode demorar aproximadamente um minuto. A plataforma não recomenda a modalidade free para produção, e existem cotas de horas, banda e builds. Se houver cartão cadastrado, pode haver cobrança por excedentes conforme termos; monitore o painel.
- **Neon Free** em outubro/2026 disponibiliza **1 GB de armazenamento por projeto**, sujeito a limites de consumo. **Anexos são armazenados no PostgreSQL** e ocupam essa cota. PSD/CDR grandes podem esgotá-la rapidamente.
- O domínio gratuito Wix continua disponível. Domínio personalizado pode ter custo.
- Use apenas uma instância de serviço para começar e mantenha cópias regulares de segurança.

## Arquivos

- `app.py` — pedidos, clientes, anexos, backup e PDFs.
- `security.py` — autenticação e permissões.
- `database.py` — transações e conexão PostgreSQL TLS.
- `requirements.txt` — driver psycopg.
- `render.yaml` — Render Free sem Persistent Disk.
- `Dockerfile` — ambiente de execução.
- `exportar_sqlite_para_json.py` — extrai backup JSON de SQLite antigo, somente leitura.
- `static/` — interface de administração original. O Wix hospeda a interface pública separadamente.

Esta adaptação foi verificada com testes locais de compatibilidade. Faça também testes reais de integração com Neon e Render antes de usar dados de clientes em produção.
