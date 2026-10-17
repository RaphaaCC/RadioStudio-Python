# 05 — Handoff para Codex

Este arquivo serve como ponto de entrada para uma futura sessão de desenvolvimento.

## Missão

Investigar e corrigir dois sintomas no pipeline de transmissão do RadioStudio Python:

1. **swishing / ruído aquoso-metálico de fundo** no stream;
2. **microtravadas / lag / pequenos cortes** ocasionais.

Não assumir previamente que o Icecast é o culpado.

## Base conhecida

Base analisada ao escrever esta documentação:

```text
branch: main
commit: 26d8642aad217b2092dc066c4f8e5674c3823c47
```

Antes de modificar qualquer coisa:

```text
1. atualizar a branch
2. reler os arquivos atuais
3. confirmar que a arquitetura ainda corresponde a estes docs
```

## Arquivos prioritários

```text
radiostudio/services/broadcast.py
radiostudio/services/broadcast_quality.py
radiostudio/ui/audio_pipeline.py
radiostudio/ui/main_window.py
radiostudio/services/audio_inputs.py
```

Quando necessário, revisar também:

```text
radiostudio/ui/playback.py
radiostudio/ui/cart_wall.py
radiostudio/ui/settings_controller.py
tests/
```

## Arquitetura atual resumida

```text
QMediaPlayer
  ↓
QAudioBufferOutput
  ↓ s16le / 44100 / stereo
PCMBuffer por fonte
  ↓
Mixer Python (20 ms)
  ↓
Limiter
  ↓
EncoderOutput PCM queue (max 120 frames)
  ↓
FFmpeg/libmp3lame
  ↓
MP3 queue (max ~2 seconds; block count follows bitrate)
  ↓
network writer → RemoteSink
  ↓
Icecast ou SHOUTcast
```

## Suspeitas principais

### Swishing

Prioridade de investigação:

```text
1. re-encoding MP3, principalmente em 128 kbps
2. política/parâmetros do libmp3lame
3. processamento de ganho/limiter se o som for mais pumping que swishing
```

### Lag / microcortes

Prioridade:

```text
1. PCMBuffer.take() completando frame insuficiente com zeros
2. jitter entre QAudioBufferOutput e mixer de 20 ms
3. EncoderOutput.queue acumulando e descartando frame antigo ao lotar
4. publish/sendall bloqueando o network writer; a fila MP3 limitada agora aciona reconexão quando satura
5. scheduling Python/Windows sob carga
```

## Regras para a investigação

Não fazer uma reescrita grande antes de localizar o ponto do defeito.

Instrumentar temporariamente e responder primeiro:

```text
O PCM imediatamente antes do encoder está limpo?
O MP3 imediatamente depois do FFmpeg está limpo?
A fila chega a 120?
Há frames PCM ou blocos MP3 descartados?
Há zero-fill durante playback estável?
Qual o tempo de escrita e a profundidade máxima das filas?
```

## Testes mínimos sugeridos

### Matriz de bitrate

```text
128
192
256
320 kbps
```

Usar a mesma faixa e o mesmo servidor.

### Fonte

Comparar:

```text
WAV/FLAC
MP3 320
MP3 de bitrate menor
```

### Destino

Comparar:

```text
servidor embutido
Icecast remoto
outro encoder -> mesmo Icecast
```

### Capturas

Salvar temporariamente para análise:

```text
PCM pré-FFmpeg
MP3 pós-FFmpeg/pré-socket
stream recebido do Icecast
```

## Não confundir sintomas

```text
swishing / warbling
≈ geralmente codec/compressão

click / dropout / engasgo
≈ geralmente descontinuidade/buffer/timing

atraso crescente + salto
≈ geralmente backlog/fila/rede

pumping / respiração de volume
≈ geralmente compressor/limiter/ganho
```

## Possíveis correções, em ordem conservadora

Somente depois de medir:

```text
1. melhorar padrão/preset de encoding e bitrate
2. corrigir underrun/jitter do PCMBuffer
3. adicionar telemetria de queue depth e drops
4. desacoplar leitura do FFmpeg da escrita de rede
5. revisar política de congestionamento/reconexão
6. avaliar mixer float interno
7. avaliar AAC-LC opcional
8. considerar passthrough MP3 apenas como evolução avançada
```

## Guardrails

Não quebrar:

- Icecast;
- SHOUTcast;
- servidor embutido;
- fades/crossfades;
- carts;
- microfone/auxiliares;
- ducking;
- metadados;
- volume local independente do stream;
- AutoDJ e scheduler indiretamente.

Não remover o limiter só para "ver se melhora" sem medir clipping.

Não aumentar buffers indefinidamente para esconder uma fila congestionada.

Não mudar mixer + encoder + protocolo ao mesmo tempo em um único passo difícil de isolar.

## Testes automatizados desejáveis

Criar/regredir testes para qualquer alteração feita. Exemplos:

```text
PCMBuffer não deve inserir silêncio indevido em cenário de jitter controlado
fila deve reportar/drop policy determinística
mixer mantém comprimento e formato exatos do frame
limiter não clipa
stop/reconnect limpa estado corretamente
Icecast chunked permanece válido
SHOUTcast permanece válido
```

Executar a suíte completa existente no Windows CI, `compileall` e smoke test Qt offscreen.

## Definition of Done

Considerar o trabalho concluído somente quando:

1. existir uma causa reproduzível identificada para o swishing e/ou lag;
2. a correção tiver teste ou evidência objetiva associada;
3. comparação A/B mostrar melhora;
4. nenhuma regressão aparecer no pipeline de transmissão;
5. a suíte existente continuar verde;
6. os docs desta pasta forem atualizados com a causa final e a solução escolhida.

## Prompt curto sugerido para iniciar a próxima sessão

```text
Leia primeiro docs/audio-streaming-bug/ inteiro. Investigue o swishing e as microtravadas da transmissão do RadioStudio sem assumir a causa. Instrumente PCM, encoder e filas para localizar o primeiro ponto em que o defeito aparece. Só depois implemente a correção mínima e valide Icecast, SHOUTcast, servidor embutido, fades/mix e os testes existentes.
```
