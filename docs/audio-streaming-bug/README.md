# Investigação: ruído "swishing" e travadas na transmissão

Esta pasta documenta um problema observado ao transmitir o RadioStudio Python para Icecast: um ruído de fundo descrito como **"swishing" / aquoso / metálico** e, em alguns momentos, **microtravadas ou lag**.

> **Importante:** esta documentação registra hipóteses, pontos suspeitos do código e um plano de diagnóstico. **Nenhuma correção é implementada aqui.** O objetivo é deixar contexto técnico suficiente para uma próxima sessão de desenvolvimento/Codex.

## Estado da investigação

Base analisada: `main` no commit `26d8642aad217b2092dc066c4f8e5674c3823c47`.

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