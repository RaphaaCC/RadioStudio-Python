# Instruções para agentes de IA

Estas orientações valem para todo o repositório RadioStudio Python Sirius. Siga também as instruções diretas do usuário, que têm precedência quando definem um escopo ou uma exceção.

## Contexto do projeto

- Aplicativo desktop para Windows feito em Python, PySide6/Qt, com reprodução de áudio, playlists, AutoDJ, Cart Wall, entradas ao vivo e transmissão.
- A pasta de código é o ambiente de desenvolvimento contínuo. Alterar `APP_VERSION` ou o código-fonte não significa que um executável foi compilado, publicado ou instalado.
- A inicialização e a instância única ficam em `radiostudio/app.py`; nomes, versão, caminhos e extensões ficam em `radiostudio/config.py`.
- A arquitetura é um único aplicativo Qt, mas tarefas em segundo plano e processos auxiliares podem existir. Não descreva o programa como um único processo do sistema sem conferir o caminho em questão.

## Fluxo de trabalho

1. Entenda o pedido e confirme o estado do repositório com `git status --short`. A árvore pode conter alterações do usuário ou de tarefas anteriores.
2. Leia este arquivo, o `README.md` quando relevante e os módulos e testes diretamente envolvidos.
3. Procure o fluxo existente com `rg` antes de editar; siga os dados desde a interface até persistência ou serviço quando necessário.
4. Faça a menor mudança que resolva o pedido. Preserve alterações não relacionadas; nunca use `git reset`, `git clean`, checkout destrutivo ou restauração ampla para “limpar” a árvore.
5. Rode primeiro os testes do subsistema alterado e, quando a lógica mudar, a suíte completa indicada em **Verificação**.
6. Ao terminar, resuma o que mudou, os testes executados e qualquer limite de validação relevante, sem narrar cada comando.

- Não crie sessões SSH repetidas: se a tarefa realmente exigir SSH, mantenha e reutilize a sessão ativa. Alterações locais não precisam abrir uma sessão remota.
- Não compile, instale, publique, faça commit, push, crie tag ou GitHub Release sem pedido explícito. O usuário cria as Releases do GitHub manualmente.
- Quando uma compilação for pedida, confirme que o usuário quer compilar o estado atual do código e use `packaging/build_release.ps1`. Não diga que o executável reflete o código se não tiver sido compilado e verificado nesta tarefa.
- Se uma tarefa mudar versão, confira todos os locais relevantes: `radiostudio/config.py`, `packaging/build_release.ps1`, `packaging/installer.iss` e outros resultados de `rg`. Não altere versão só por preparar ou discutir um lançamento.
- Faça stage seletivo se um commit tiver sido explicitamente pedido; nunca inclua automaticamente arquivos já modificados sem relação com o pedido.

## Mapa do código

| Área | Pontos de entrada | Responsabilidade |
| --- | --- | --- |
| Inicialização | `radiostudio/app.py`, `radiostudio/config.py` | QApplication, idioma inicial, instância única, caminhos e constantes compartilhadas. |
| Janela principal | `radiostudio/ui/main_window.py`, `radiostudio/ui/_shared.py` | Montagem da janela `RadioStudio`, estado compartilhado e mixins da interface. |
| Playlist e reprodução | `radiostudio/ui/playlist.py`, `playlist_table.py`, `playback.py` | Fila, reordenação, seleção, decks, falhas de mídia e transições de faixa. |
| Áudio e mix | `radiostudio/ui/audio_pipeline.py`, `studio_widgets.py`, `radiostudio/services/transition_analysis.py` | Buffers PCM, níveis, monitoramento, visualizadores e análise de transições. |
| Entradas ao vivo | `radiostudio/ui/audio_inputs.py`, `audio_inputs_panel.py`, `radiostudio/services/audio_inputs.py` | Interface/controlador e captura/conversão por canal. |
| Transmissão | `radiostudio/ui/broadcast_panel.py`, `radiostudio/services/broadcast.py`, `broadcast_quality.py` | Configuração, encoder, servidor embutido e destinos externos. |
| Cart Wall | `radiostudio/ui/cart_wall.py` | Atribuição e disparo dos carts. |
| AutoDJ e biblioteca | `radiostudio/ui/autodj_panel.py`, `autodj_controller.py`, `radiostudio/services/autodj.py`, `music_library.py` | Seleção, filas, regras, indexação e banco SQLite da biblioteca. |
| Agenda | `radiostudio/ui/scheduler.py`, `event_dialog.py` | Eventos programados e sua edição/execução. |
| Configurações | `radiostudio/ui/settings_dialog.py`, `settings_controller.py` | Widgets, validação, aplicação das opções e ações das configurações. |
| Estado persistente | `radiostudio/ui/persistence.py`, `radiostudio/services/backups.py` | Leitura/gravação do estado, recuperação, importação e exportação de backups. |
| Metadados e capas | `radiostudio/services/track_metadata.py`, `cover_art.py` | Leitura de tags, formatação de títulos e carregamento de capas. |
| Segurança operacional | `radiostudio/runtime_safety.py`, `ui/message_boxes.py` | Tratamento isolado de exceções e mensagens úteis na interface. |
| Idiomas | `radiostudio/i18n.py`, `radiostudio/translations/*.json` | Preferência de idioma e catálogos da interface. |

`main_window.py` combina mixins para manter a janela principal montada por áreas. Lógica sem interface pertence preferencialmente a `services/`; não mova código entre camadas como parte de uma correção pontual.

## Regras de comportamento e segurança

### Áudio, reprodução e concorrência

- Preserve o caminho de áudio compartilhado entre reprodução, Master Ao Vivo e transmissão. O volume de Monitoramento afeta somente a escuta local; baixar o Master Ao Vivo afeta o sinal entregue às saídas.
- A atenuação do microfone aplica-se à música da playlist durante a transmissão; não multiplique por ela o ganho do microfone ou dos auxiliares. Desativar Monitoramento local não pode parar a captura nem silenciar as saídas de transmissão.
- Entradas ao vivo usam captura e conversão PCM independentes por canal. Uma falha de dispositivo não deve interromper outros canais, reprodução ou transmissão.
- Não bloqueie callbacks de áudio com I/O de disco/rede, análise pesada ou trabalho de interface. Mantenha cálculos e operações lentas fora da thread Qt principal quando possível.
- Widgets só podem ser atualizados na thread da interface. Workers entregam resultados por sinais Qt ou timers; evite alterar a playlist dentro de callbacks de mídia reentrantes.
- Ao mudar reprodução, transições, AutoDJ ou encerramento, considere pausa, avanço manual, fim natural, troca de faixa, caminhos duplicados, arquivo ausente, falha de decodificação, retomada e encerramento durante uma tarefa.
- Falha de encoder, saída, dispositivo ou integração opcional deve ser isolada sempre que possível; registre detalhes técnicos no logger `radiostudio` e mostre uma mensagem compreensível.
- Não abra microfones físicos, não inicie reprodução do usuário e não inicie transmissões reais em testes automatizados. Use PCM sintético e doubles/mocks.

### Dados, caminhos e credenciais

- `radiostudio/config.py` define os caminhos. Instalação usa `%APPDATA%\RadioStudioPython`; edição portable usa `RadioStudioPythonData` ao lado do executável quando gravável e recorre ao perfil do usuário se não for.
- Nunca use nem modifique o perfil real `%APPDATA%\RadioStudioPython` durante testes. Isole a execução com `APPDATA` em uma pasta temporária exclusiva.
- Não leia, imprima, registre, copie para backup ou versione senhas e tokens. Credenciais de transmissão são guardadas no Gerenciador de Credenciais do Windows; backups não as exportam.
- Ao alterar formatos de estado ou banco, preserve compatibilidade com dados existentes, trate JSON/SQLite corrompidos e faça backup ou migração recuperável antes de qualquer substituição. Nunca apague o banco real como atalho de teste.
- Normalize caminhos de áudio com os helpers existentes em `radiostudio/services/audio_paths.py`; considere Windows sem diferenciar maiúsculas/minúsculas e caminhos equivalentes.

### Interface e internacionalização

- Toda nova string estática da interface deve usar `translate(...)` e existir em todos os catálogos: `pt_BR`, `en_US`, `es_MX`, `ko_KR`, `ja_JP`, `zh_TW` e `zh_CN`.
- O português brasileiro é a língua-fonte das chaves; `pt_BR.json` mantém cada valor igual à chave. Uma chave ausente em outro catálogo aparece no idioma-fonte. Já a escolha automática do sistema usa o mapeamento de `i18n.py` e tem inglês como fallback para idioma de sistema não reconhecido.
- Traduza rótulos, títulos, tooltips, diálogos, mensagens de erro e itens dinâmicos definidos pelo programa. Não traduza nomes de arquivos, tags, URLs, conteúdo digitado pelo usuário ou dados recebidos de serviços.
- Preserve placeholders de tradução e de metadados, bem como argumentos e valores internos de `QComboBox`; traduza o rótulo, não o valor salvo.
- A troca de idioma deve manter a confirmação na língua escolhida e seguir o fluxo existente de reinício. Mudanças de catálogo exigem validar paridade e placeholders em `tests/test_i18n.py`.
- Para uma função referenciada pelo RadioBOSS, consulte o manual oficial da função antes de implementar. Use-o como referência de comportamento, sem copiar a interface nem tornar o RadioStudio dependente do RadioBOSS.

## Testes e validação

Use sempre o Python do projeto: `.venv\Scripts\python.exe`. Não instale dependências nem atualize pacotes sem necessidade para o pedido.

| Mudança | Testes para localizar primeiro |
| --- | --- |
| Playlist, fila, reprodução ou falha de mídia | `test_playlist_regressions.py`, `test_playback_ui_regressions.py`, `test_playback_errors.py` |
| Fades, Mix, análise e visualizadores | `test_fade_mix_regressions.py`, `test_transition_analysis.py`, `test_waveform_performance.py` |
| Transmissão e encoder | `test_broadcast_startup.py`, `test_broadcast_signal_regressions.py`, `test_broadcast_quality.py` |
| Cart Wall | `test_cart_wall_regressions.py` |
| AutoDJ e histórico/fila | `test_autodj.py`, `test_autodj_queue_regressions.py`, `test_autodj_startup.py`, `test_autodj_history_ui.py` |
| Entradas e áudio | `test_audio_inputs.py`, `test_audio_paths.py`, `test_audio_decode_fallback.py` |
| Estado, configurações ou backup | `test_persistence_regressions.py`, `test_settings_dialog_regressions.py`, `test_backups.py` |
| Idioma | `test_i18n.py` |

- Depois de mudar lógica, rode os testes afetados e a suíte completa: `python -m unittest discover -s tests -v`.
- Para mudanças de interface, abra e feche a janela afetada com `QT_QPA_PLATFORM=offscreen`; use um `APPDATA` temporário. Confirme também estado inicial, valores restaurados e cancelamento/salvamento quando pertinentes.
- Exemplo em PowerShell para isolar dados: `$env:QT_QPA_PLATFORM = 'offscreen'; $env:APPDATA = Join-Path $env:TEMP ('RadioStudioPython-test-' + [guid]::NewGuid().ToString('N'))`.
- Para integrações externas, mocks/testes locais não comprovam interoperabilidade real. Só declare um servidor, cliente Discord ou dispositivo como validado se foi realmente testado nesse ambiente e com autorização.
- Evite testes redundantes que apenas repetem a implementação. Amplie a verificação somente para cobrir um risco concreto ou uma exigência deste arquivo.

## Documentação, licença e distribuição

- O `README.md` público é escrito em inglês; mantenha essa língua e o estilo existente quando uma mudança funcional exigir atualização. Não o altere para refatorações internas, nem quando o usuário pedir para deixá-lo quieto.
- Atualize o manual somente quando o uso visível mudar. O manual oficial do aplicativo é `radiostudio/assets/manual_usuario.pdf`; não crie cópias concorrentes em `output/`.
- O projeto é source-available sob a licença presente em `LICENSE`, não o descreva como open source. Não altere licença, créditos ou direitos sem solicitação explícita.
- Não prometa suporte, compatibilidade, ausência de bugs ou economia de recursos além do que foi efetivamente verificado.
