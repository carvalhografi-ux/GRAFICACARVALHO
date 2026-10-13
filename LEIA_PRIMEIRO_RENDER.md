# Gráfica Carvalho — migração do Railway para o Render + Wix

## Situação
A interface está publicada no Wix:
https://headless-fynqkyfsoyi-carvalhografi-140d.wix-site-host.com
O backend Python deve ser publicado em uma instância Web Service do Render com **disco persistente**. Os pedidos e anexos ficam no SQLite armazenado em `/data/grafica_carvalho.db`.

**Atenção: o pacote NÃO é um serviço online já implantado.** Não envie o banco real, backup JSON ou senhas ao GitHub.

## Passos na Render

1. Crie uma conta em https://dashboard.render.com e conecte o GitHub.
2. Extraia esse ZIP e envie o conteúdo da pasta para um repositório **privado**. Configure o Render para usar o arquivo `render.yaml` (*Blueprint* / *New Blueprint Instance*), que cria um Web Service Docker com **disco persistente de 1 GB montado em `/data`**. O plano é pago e a cobrança precisa ser aprovada por você no Render.
3. Defina `GRAFICA_ADMIN_EMAIL` (seu e-mail) e `GRAFICA_ADMIN_PASSWORD` (senha forte inédita de pelo menos 12 caracteres) na configuração, sem colocá-los no GitHub. Após inicializar e entrar com sua conta, retire a senha inicial do painel de variáveis.
4. O endereço real do serviço será exibido pelo Render no formato `https://NOME-REAL.onrender.com`. Não pressuponha que um nome desejado está disponível.
5. Abra `https://NOME-REAL.onrender.com/healthz` e confira a resposta `{"ok": true}`.
6. Informe o **endereço HTTPS exato**, sem `/healthz`, nesta conversa. Atualizaremos o arquivo `config.js` do site Wix já publicado, preservando seu endereço e identidade.
7. No painel administrativo, use **Restaurar backup** para importar o JSON exportado da antiga instalação, conferindo clientes, anexos e códigos. O banco antigo não é transferido sozinho. Se houver usuários existentes em outro servidor, migre-os por procedimento especializado, nunca copiando um SQLite em uso.
8. Faça backups periódicos do volume e exports externos. Verifique os custos e espaço disponível.

## Arquitetura
- Wix: páginas HTML/CSS/JS e logotipo; domínio público atual acima.
- Render: backend Python protegido; rotas `/api/*`, autenticação e geração PDF A4/A5.
- Disco Render: SQLite e anexos em `/data/grafica_carvalho.db` (somente uma instância).
- `GRAFICA_WIX_ORIGIN`: restrição CORS ao endereço HTTPS Wix correto.
- `GRAFICA_SECURE_COOKIES=1`: cookies marcados Secure no backend.

## Nome de domínio personalizado
Recomendação opcional: `sistema.graficacarvalho.com.br` para o site Wix e `api.graficacarvalho.com.br` para a API Render, **somente se você possuir/controlar `graficacarvalho.com.br`** e configurar os registros DNS. Nenhum desses domínios está registrado ou vinculado por este pacote.

## Proteções
- O aplicativo não inicia no Render se `/data` não estiver realmente montado.
- O `render.yaml` cria disco persistente; o plano gratuito não é recomendado nem compatível com esse uso de SQLite.
- Não usar múltiplas réplicas: SQLite no mesmo disco deve ser usado por uma instância única.
- Autenticação separada para admin, equipe e portal do cliente já implementada; recomendada revisão de segurança independente antes de atender clientes reais.
- Não exponha as credenciais de administradores nem compartilhe tokens/senhas nesta conversa.
