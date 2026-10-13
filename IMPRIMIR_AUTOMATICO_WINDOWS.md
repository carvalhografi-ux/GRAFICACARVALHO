# Gráfica Carvalho — impressão automática no Windows

O sistema Wix/Render/Neon pode enviar ordens de serviço diretamente à impressora **sem abrir o PDF**. Uma máquina **Windows 10/11** na mesma rede das impressoras deve executar o agente gratuito `agente_impressao.py` com o SumatraPDF instalado.

**Os endereços informados não são endpoints de impressão direta:**
- Impressora 1: `http://[fe80::6a55:d4ff:fe3f:2f3f%5]:80/WSD/DEVICE` (IPv6 link-local; %5 é índice da placa de rede no Windows)
- Impressora 2: `http://192.168.0.105:80/WSD/DEVICE` (IPv4 LAN)
Esses dois links podem apontar para **a mesma impressora física**. É necessário verificar os **nomes dos dispositivos instalados em Configurações → Impressoras e scanners do Windows**.

## Segurança e operação

- O Render não tenta abrir conexão aos IPs privados da gráfica. O agente faz **apenas conexões HTTPS de saída** com token próprio.
- Somente administrador pode gerar/rotacionar chave do agente. Admin e staff podem colocar OS na fila; conta cliente não pode imprimir.
- O token é mostrado uma única vez, armazenado como SHA-256 no Neon e em arquivo local privado do usuário Windows, jamais no HTML.
- O servidor gera PDF no momento do clique, guarda em fila no Neon e o agente chama SumatraPDF.exe com `-print-to` e `-silent`, sem janela.
- Não há reimpressão automática após falha do Windows para evitar cópias duplicadas.
- Status "sent" significa **entregue ao spooler do Windows**; não confirma que saiu papel físico.
- O PC precisa ficar ligado, com drivers, impressoras instaladas e SumatraPDF disponível. Enquanto desligado, botão de impressão direta fica indisponível; baixar PDF segue funcionando.

## Instalação

1. Instale Python 3 com o comando `py` disponível no Windows.
2. Instale **SumatraPDF gratuito** a partir do site oficial: https://www.sumatrapdfreader.org/download-free-pdf-viewer
3. Extraia a pasta `Agente_Impressao_Windows` do pacote de instalação.
4. No Wix, entre como administrador, abra **Imprimir OS**, escolha **Gerar chave para conectar computador** e copie a chave privada para o agente; não publique essa chave.
5. Execute `CONFIGURAR_IMPRESSORAS.bat` para selecionar **dois nomes diferentes** de impressoras instaladas no Windows, cole a chave.
6. Inicie `INICIAR_IMPRESSAO.bat` e mantenha essa janela aberta.
7. Clique em imprimir no Wix, escolha impressora, formato A5/A4/duas A5 em A4 e cópias. A fila leva em geral alguns segundos após conectar.
8. Teste **uma OS de teste** antes de imprimir pedidos de clientes.

As mudanças no Render **não alteram** pedidos, anexos, valores ou usuários já cadastrados.
