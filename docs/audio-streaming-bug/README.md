# Investigação: ruído "swishing" e travadas na transmissão

Esta pasta documenta um problema observado ao transmitir o RadioStudio Python para Icecast: um ruído de fundo descrito como **"swishing" / aquoso / metálico** e, em alguns momentos, **microtravadas ou lag**.

> **Importante:** swishing e microtravadas ainda são sintomas sem causa confirmada. As mudanças abaixo reduzem alguns riscos e adicionam métricas, mas não substituem o teste A/B com uma faixa e um destino reais.

## Estado da investigação

Base original analisada: `main` no commit `26d8642aad217b2092dc066c4f8e5674c3823c47`. O código evoluiu desde então; confira a árvore atual antes de usar os detalhes como descrição do estado presente.

### Salvaguardas adicionadas em 2026-10-17

- O `PCMBuffer` pré-carrega dois frames (40 ms) antes de começar uma fonte e conta aquecimento, underruns, preenchimento com zeros e descartes.
- O encoder usa 192 kbps como padrão de novos perfis e oferece níveis de complexidade do `libmp3lame`.
- O FFmpeg recebe `-probesize 32 -analyzeduration 0` para o PCM cuja taxa, canais e formato são conhecidos; um teste ao vivo sintético confirmou saída MP3 antes do fim do pipe.
- Os blocos MP3 foram reduzidos a 1024 bytes para diminuir o tempo de espera antes de publicar cada lote. A leitura do FFmpeg e a escrita de rede são separadas por uma fila limitada. Se a rede não acompanhar e essa fila lotar, a saída reconecta para evitar atraso sem limite; isso ainda pode causar uma lacuna durante congestionamento.
- Quando a fila MP3 enche ou a publicação falha, o processo FFmpeg é encerrado para também desbloquear uma eventual escrita PCM pendente; isso evita deixar a thread de transmissão presa.
- O endpoint embutido `/status.json` expõe métricas do mixer, buffers PCM, filas, descartes e duração das escritas de rede.
- O fechamento de uma saída interrompe com segurança uma escrita de rede bloqueada; o término HTTP chunked só é enviado quando o writer já terminou.
- O callback do player enfileira o PCM antes do trabalho visual; a animação reativa é limitada a 30 Hz e não inicia outra decodificação da faixa inteira só para desenhar uma forma estática.

Os testes automatizados cobrem essas políticas com PCM sintético e sockets falsos. A espera de análise de entrada era uma causa reproduzível de atraso para o primeiro MP3, e foi reduzida. Swishing, underruns em reprodução real e a qualidade auditiva após a rede ainda precisam de teste A/B com uma faixa e destino reais; não há confirmação de uma correção sonora completa.

O pipeline atual separa responsabilidades assim:

```text
Arquivo de áudio
  ↓
Qt Multimedia / QMediaPlayer
  ↓ decodificação
QAudioBufferOutput
  ↓ PCM 44.1 kHz / estéreo / int16
Mixer Python do RadioStudio
  ↓
Limiter do barramento de transmissão
  ↓
FFmpeg + libmp3lame
  ↓ MP3 CBR
Socket do RadioStudio
  ↓
Icecast / SHOUTcast
  ↓
Ouvinte
```

Se o Qt reportar `InvalidMedia`, `AudioDecodeFallback` tenta decodificar a fonte com FFmpeg para um WAV temporário e entrega o arquivo ao player. A conversão também é usada no Mix, nos carts e na pré-escuta; não altera o fluxo PCM já entregue ao encoder da transmissão.

A hipótese mais forte para o **swishing** é degradação na etapa de **re-encoding MP3**, especialmente quando a fonte já é MP3 e a saída está configurada em bitrate baixo/moderado, por exemplo 128 kbps.

As hipóteses mais fortes para **microtravadas / cortes / estalos** estão relacionadas a **buffering e temporização do PCM**, à fila entre mixer e encoder e a bloqueios/oscilações no envio de rede.

## Documentos

- [`01-arquitetura-atual.md`](01-arquitetura-atual.md) — como o áudio percorre o programa hoje.
- [`02-sintomas-e-hipoteses.md`](02-sintomas-e-hipoteses.md) — sintomas e causas prováveis, separadas por nível de confiança.
- [`03-plano-de-diagnostico.md`](03-plano-de-diagnostico.md) — testes para determinar exatamente onde o defeito aparece.
- [`04-opcoes-de-correcao.md`](04-opcoes-de-correcao.md) — caminhos de correção priorizados e riscos.
- [`05-handoff-codex.md`](05-handoff-codex.md) — resumo operacional para uma futura sessão no Codex.

## Arquivos de código relevantes

Principalmente:

```text
radiostudio/services/broadcast.py
radiostudio/services/broadcast_quality.py
radiostudio/ui/audio_pipeline.py
radiostudio/ui/main_window.py
radiostudio/services/audio_inputs.py
```

Também podem ser relevantes os módulos de playback/transições quando o defeito acontecer durante fades e crossfades.

## Conclusão provisória

Não há evidência suficiente para atribuir o problema ao servidor Icecast sozinho. O RadioStudio já entrega ao Icecast um stream MP3 codificado; portanto, artefatos que nasçam antes ou durante a codificação chegam ao servidor prontos para serem distribuídos.

O diagnóstico deve separar três classes de falha:

1. **Artefato de codec** — swishing/warbling típico de compressão perceptiva.
2. **Underrun/temporização** — zeros ou descontinuidades inseridos no PCM, causando cliques/engasgos.
3. **Transporte/rede** — bloqueio da saída, fila acumulando ou descarte de frames quando o envio não acompanha a produção.

A próxima sessão deve medir essas três partes separadamente antes de redesenhar o pipeline.
