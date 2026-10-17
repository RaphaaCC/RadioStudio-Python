# RadioStudio Python 1.0.0 — Sirius

Aplicativo de automação de áudio para Windows, inspirado nos fluxos descritos no manual do RadioBOSS 7.2. Projeto independente, com interface própria.

A interface acompanha automaticamente o tema claro ou escuro do Windows, inclusive no explorador e no histórico.

## Executar

Abra `Iniciar RadioStudio Python.bat` com duplo clique. O código está organizado no pacote `radiostudio/` por interface, serviços de áudio e inicialização. Ele cria o ambiente Python e instala as dependências na primeira execução.

### Organização do código

- `radiostudio/app.py` e `radiostudio/config.py`: inicialização e caminhos compartilhados.
- `radiostudio/ui/`: janela, explorador, fila, reprodução, transmissão, agendador e componentes visuais.
- `radiostudio/services/`: transmissão/áudio e leitura de metadados.
- `radiostudio/runtime_safety.py`: isolamento de falhas em ações da interface e log técnico com rotação.
- `main.py`: ponto de entrada compatível; o arquivo BAT inicia o pacote diretamente.
- `AGENTS.md`: orientações para agentes de IA sobre arquitetura, segurança dos dados, áudio e verificações.

## Funções desta versão

- Playlist com importação de arquivos e pastas, reordenação por arraste, remoção de várias faixas com `Ctrl+A` + `Delete`, movimento da seleção com `Ctrl+↑/↓` e salvamento em M3U. Título, artista e álbum são lidos das tags dos arquivos; quando não há título, aparece o nome do arquivo.
- Explorador de arquivos na lateral esquerda, em árvore compacta de pastas e áudios: expanda pastas pelo botão `+` ou duplo clique, dê duplo clique em um áudio ou arraste-o para a playlist **Now Playing**. A barra com emojis permite escolher a pasta principal, ver os discos, subir uma pasta, voltar à pasta de músicas, filtrar arquivos pelo nome e mostrar o campo de caminho com 📍. O local onde o áudio é solto define a posição na fila. Também é possível selecionar vários arquivos e clicar em **Adicionar seleção**.
- Abaixo do explorador, o histórico mostra quando uma música começou a tocar, conexões e desconexões das saídas de transmissão e erros. Os eventos são mantidos em `%APPDATA%\RadioStudioPython\events.jsonl`, os 500 mais recentes aparecem na interface e falhas técnicas ficam em `%APPDATA%\RadioStudioPython\app.log` com rotação automática.
- Janela **Configurações** com seleção da saída principal e do dispositivo de escuta. O botão **Pré-escutar seleção** (ou o menu do botão direito na playlist) usa a saída de escuta sem enviar o áudio à transmissão.
- Em **Configurações**, a opção **Ouvir o áudio master neste computador** pode ser desativada para transmitir a playlist e o cart wall sem reproduzi-los localmente. A pré-escuta continua independente. O volume do player controla somente a monitoração local e não reduz o nível do programa enviado à transmissão.
- Aba **🎙 Entradas** com uma entrada de microfone e cinco auxiliares independentes. Escolha os dispositivos em **Configurações > Entradas**; os botões ativam ou removem cada entrada do master e da transmissão. A imagem `radiostudio/assets/microphone.png` é o ícone do botão do microfone. O microfone reduz o nível da música enquanto está ativo e pode aplicar uma redução adicional ao detectar fala, com limite e níveis configuráveis; auxiliares não alteram a música. A escuta local das entradas acompanha a opção do áudio master, enquanto a captura para transmissão permanece ativa.
- Em **Configurações > Metadados**, defina nome, descrição, gênero e site da estação e personalize o título enviado aos ouvintes. O campo **Título no ar** aceita `%title`, `%artist`, `%album`, `%albumartist`, `%genre`, `%year`, `%track`, `%composer`, `%filename`, `%duration` e `%station`; `%%` insere um `%` literal. A janela mostra uma prévia com a faixa atual. Exemplo: `%station | %artist - %title (%album)`.
- A barra de menus no topo reúne **Arquivo**, **Reprodução**, **Transmissão**, **Configurações** e **Ajuda**. Em **Configurações > Geral**, você pode escolher se fechar a janela a oculta na bandeja, se a faixa que estava tocando deve ser retomada e se a transmissão ativa deve reiniciar na próxima abertura. Ao sair de verdade, a posição da música e o estado da transmissão são salvos; **Sair** no menu da bandeja ou em **Arquivo** encerra o programa. Se ele já estiver na bandeja, abrir o `.bat` novamente traz a janela existente para frente.
- Em **Configurações > Fades e Mix**, ajuste o fade in ao iniciar uma faixa, o fade out ao parar/avançar, a sobreposição de faixas no Mix e a detecção de silêncio perto do fim para antecipar a próxima faixa. Fade e Mix são tratados separadamente: configurar fade não cria uma sobreposição automaticamente; o tempo de **Mix** controla a sobreposição. Os fades usam curvas de potência suave e atualização de ganho a 100 Hz. A opção de transições automáticas analisa intro, final e nível de áudio em segundo plano para escolher a duração do Mix e o fade; caudas realmente silenciosas recebem uma entrada curta para a próxima música em vez de um fade excessivamente longo. A faixa atual só é promovida quando o áudio chega ao fim. O corte por silêncio monitora apenas o trecho final (até 15 segundos ou 25% da faixa) e usa limite em dB e tempo mínimo configuráveis.
- O painel superior mostra data, hora com segundos e a contagem até a próxima hora. A área **No ar** desenha a forma de onda calculada do arquivo em reprodução e acompanha a posição e o tempo restante da faixa. Em **Configurações > Geral**, escolha entre seis estilos de forma de onda e quatro visores reativos: onda ao vivo, espectro por frequência, espectro espelhado e curva de frequências. O indicador **NO AR** pisca em vermelho enquanto uma saída de transmissão estiver ativa.
- Os controles de reprodução ficam abaixo da playlist. Em **Configurações > Geral > Controles do player**, é possível movê-los para o topo; a posição escolhida é salva.
- Reprodução local, pausa, próxima faixa, volume e barra de posição. O slider de volume mantém largura fixa para continuar preciso quando a área do player é estreita.
- Cart wall com 25 botões, atribuição de áudio e reprodução independente.
- Agendador diário ou semanal para inserir e tocar um arquivo em horário definido.
- Aba **AutoDJ** com bibliotecas nomeadas e indexadas em segundo plano, programação semanal recorrente por dias e intervalos (inclusive blocos que cruzam a meia-noite), prioridade entre blocos e filtros de gênero, artistas, palavras incluídas/excluídas e ano. Fora dos blocos, o AutoDJ escolhe aleatoriamente entre as faixas elegíveis de todas as bibliotecas habilitadas, favorecendo as menos tocadas; dentro deles, respeita a seleção e os filtros do bloco. Ele completa a fila sem duplicar a mesma faixa já enfileirada.
- Regras AutoDJ para evitar repetir artista e faixa em janelas de tempo configuráveis. O algoritmo prefere faixas menos recentes, considera também os artistas e faixas que já estão na fila futura e flexibiliza cooldowns com aviso se o acervo não tiver alternativas suficientes, para não interromper a programação. Há também uma opção para remover da Now Playing as linhas anteriores à faixa atual; a música atual e as próximas ficam na fila, e o histórico continua guardado.
- Histórico de reprodução persistente em `%APPDATA%\RadioStudioPython\music_library.sqlite3`: horário local, caminho e snapshot de título, artista, álbum, gênero, ano e demais metadados de cada faixa que efetivamente começou. O histórico inclui músicas manuais e AutoDJ, alimenta os cooldowns e mantém até 50 mil execuções; também pode ser consultado e limpo na aba **AutoDJ > Histórico**. As preferências e blocos ficam em `state.json`; os dados indexados das bibliotecas ficam no SQLite.
- Configurações salvas automaticamente em `%APPDATA%\RadioStudioPython\state.json`.
- Aba **Transmissão** com perfis Icecast 2 (HTTP PUT) e SHOUTcast (fonte v1, aceita por servidores DNAS v2 compatíveis), cada um com conexão, codificador MP3 e bitrate próprio.
- Reconexão automática em caso de queda, estado de cada saída e consulta de ouvintes quando o servidor oferece uma API pública de estatísticas.
- Servidor embutido de escuta em `/live.mp3`, com limite de ouvintes e opção de acesso pela rede local; `/status.json` mostra faixa atual e ouvintes locais.
- A playlist, Mix, cart wall e entradas são somados para envio. O barramento de transmissão usa limitação de pico com pequeno headroom para evitar a distorção por hard clipping quando duas ou mais fontes se sobrepõem. O título da faixa aparece no servidor embutido via metadados ICY. Servidores externos podem receber o título quando uma senha de administração é configurada no perfil.
- Os dados da estação são usados no servidor embutido e nas novas conexões externas. Deixe em branco os campos correspondentes de um perfil externo ou o nome do servidor embutido para usar os dados definidos em **Configurações > Metadados**. A troca do formato do título afeta imediatamente a faixa em reprodução; alterações dos dados da estação em servidores externos entram em vigor na próxima conexão.

## Configurar transmissão

1. Abra a aba **Transmissão**.
2. Para o servidor embutido, marque **Ativar servidor de escuta**, escolha a porta e pressione **Iniciar transmissão**. O endereço é exibido na própria aba. A opção de rede local permite conexões de outras máquinas; regras de firewall podem ser necessárias.
   Se a porta escolhida já estiver em uso, o aplicativo tenta as próximas 20 portas e atualiza o endereço mostrado, a configuração salva e o histórico. Use o novo endereço exibido na aba.
3. Para Icecast, adicione um perfil com servidor, porta, mount, usuário de origem e senha. Para SHOUTcast, selecione **SHOUTcast (fonte v1/v2 compatível)** e informe a porta de origem oferecida pelo servidor. Servidores DNAS 2.4.7 ou mais novos podem associar a fonte v1 a um Stream ID específico.
4. A senha de origem e a senha admin opcional são guardadas no Gerenciador de Credenciais do Windows; não aparecem no `state.json`. A porta HTTP/admin só precisa ser preenchida quando difere da porta de origem.

## Configurar bibliotecas e AutoDJ

1. Na aba **🤖 AutoDJ > Bibliotecas**, adicione uma pasta. O primeiro exame e os exames manuais rodam em segundo plano; habilite as bibliotecas que podem participar da seleção aleatória.
2. Em **Programação**, crie blocos com dias, intervalo, prioridade e filtros. Horários que passam da meia-noite são aceitos. Uma biblioteca vazia no bloco significa usar todas as bibliotecas habilitadas; filtros sem resultados ficam visíveis no status do AutoDJ e não são substituídos por uma seleção fora do bloco.
3. Ligue **AutoDJ**. Ele completa a fila até o número de faixas configurado, respeita as janelas de artista/faixa também entre os itens ainda não tocados e registra cada início no histórico. A faixa que já está tocando e os itens futuros já enfileirados não são duplicados. O estado ligado é mantido ao reiniciar o aplicativo.
4. Se adicionar ou alterar arquivos dentro das pastas, use **Examinar** para atualizar o índice. Consulte ou limpe execuções em **Histórico**.

O formato de transmissão atual é MP3 estéreo, 44,1 kHz, com bitrate configurável. A opção TLS usa certificados válidos do servidor remoto. Para habilitar HTTPS no servidor embutido, coloque `server_cert.pem` e `server_key.pem` em `%APPDATA%\RadioStudioPython` e reinicie o aplicativo.

Esta é uma base funcional. O protocolo de fonte SHOUTcast implementado é o legado v1, inclusive a extensão `password:#SID` aceita por DNAS 2.4.7 ou mais novo; o wire protocol nativo v2 ainda não está implementado. Ainda não há AAC/Opus, proxies, relays de estatísticas, arquivo da transmissão, publicidade, processamento avançado de áudio ou todos os comandos e opções do RadioBOSS. A conexão com servidores externos depende das credenciais e da configuração do serviço e precisa ser confirmada com um servidor real. A disponibilidade simultânea de vários microfones/auxiliares também depende do driver e dos dispositivos instalados no Windows.
