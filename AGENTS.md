# Orientações para agentes de IA

Estas instruções se aplicam ao projeto RadioStudio Python.

## Antes de alterar

- Leia o `README.md` e os módulos diretamente envolvidos na mudança.
- Preserve a arquitetura atual: janela e controladores em `radiostudio/ui/`, serviços sem interface em `radiostudio/services/` e inicialização em `radiostudio/app.py`.
- Para uma função referenciada pelo RadioBOSS, consulte primeiro o manual oficial para entender o comportamento esperado. Mantenha o RadioStudio como um aplicativo independente, com interface própria.
- Faça a menor alteração que resolva o problema relatado. Evite refatorações amplas durante correções pontuais.

## Segurança e dados do usuário

- Não leia, mostre, registre ou versione senhas e outros segredos. Credenciais de transmissão pertencem ao Gerenciador de Credenciais do Windows.
- Não apague nem altere o perfil real em `%APPDATA%\RadioStudioPython` durante testes. Para testes de inicialização, direcione `APPDATA` a uma pasta temporária isolada.
- Não inicie transmissões reais, conecte-se a servidores externos ou publique builds sem pedido explícito.
- Trate falhas de uma saída ou componente opcional sem derrubar a reprodução e as demais saídas. Registre detalhes técnicos no logger do aplicativo e mostre mensagens úteis na interface.

## Áudio, interface e concorrência

- Preserve o caminho de áudio compartilhado pela reprodução local e pela transmissão; alterações de volume local não devem silenciar a transmissão.
- Entradas ao vivo usam captura independente por canal e conversão para PCM master. Falha em um dispositivo não deve encerrar os demais nem a reprodução. Não abra microfones reais em testes automatizados: teste conversores com PCM sintético e valide a interface sem capturar áudio do usuário.
- A atenuação do microfone se aplica à música da playlist durante a transmissão; não multiplique o ganho de microfone ou auxiliares pela atenuação da música. Desativar a escuta local precisa preservar a captura e o envio às saídas.
- Atualize widgets somente na thread da interface. Entregue resultados de workers por sinais Qt ou timers e evite mutar a playlist dentro de callbacks de mídia reentrantes.
- Ao modificar transições, reprodução, AutoDJ ou encerramento, considere pausa, troca de faixa, arquivo ausente, falha de decodificação e retomada do aplicativo.
- Mantenha operações lentas de biblioteca e análise fora da thread da interface.

## Verificação

- Use o ambiente do projeto: `.venv\Scripts\python.exe`.
- Depois de alterar lógica, execute `python -m unittest discover -s tests -v` pelo Python do ambiente.
- Para mudanças de interface, valide também a criação e o fechamento da janela com `QT_QPA_PLATFORM=offscreen` e um `APPDATA` temporário.
- Ao concluir, informe o comportamento corrigido e os testes executados; não afirme que transmissão externa foi validada sem testar contra um servidor real.

## Documentação

- Atualize o `README.md` quando mudar uma função visível, configuração, limitação ou procedimento de uso.
- Escreva a documentação em português claro, com nomes de opções iguais aos da interface e caminhos de dados explícitos.
