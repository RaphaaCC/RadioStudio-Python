# 01 — Arquitetura atual do áudio de transmissão

Este documento descreve o caminho técnico atual do áudio até Icecast/SHOUTcast e destaca onde cada transformação acontece.

## 1. Reprodução e decodificação

O player principal usa **Qt Multimedia / PySide6**. Em `radiostudio/ui/main_window.py`, o programa cria `QMediaPlayer`, `QAudioOutput` e `QAudioBufferOutput`.

O formato solicitado para o barramento de transmissão é:

```text
Sample rate: 44100 Hz
Canais:      2 (estéreo)
Formato:     Int16
```

O backend Qt Multimedia instalado decodifica os formatos que suporta e entrega PCM ao RadioStudio. A lista de extensões do explorador é mais ampla que a garantia de decodificação. Se o Qt reportar `InvalidMedia`, `AudioDecodeFallback` tenta decodificar o arquivo com o FFmpeg incluído e cria um WAV PCM temporário para o player. Isso se aplica à playlist, ao Mix, aos carts e à pré-escuta; os arquivos temporários são liberados quando o player troca ou fecha a mídia. O FFmpeg também é usado pelo encoder e pela análise de transições.

A saída local e a transmissão são caminhos relacionados, mas não idênticos. O volume master local não deve ser interpretado como ganho obrigatório do stream; o pipeline de transmissão usa seus próprios ganhos de programa/transição.

## 2. Captura do PCM do player

`radiostudio/ui/audio_pipeline.py` recebe os `QAudioBuffer` gerados pelo Qt.

O método central é:

```text
handle_player_audio_buffer(...)
```

Quando o player principal está tocando, o buffer segue para:

```text
forward_buffer("main", buffer, self.main_gain)
```

Durante um mix/crossfade, o segundo player envia:

```text
forward_buffer("mix", buffer, self.mix_gain)
```

Carts usam IDs do tipo `cart-*`; entradas de áudio usam `input:*`.

`forward_buffer()` rejeita formatos incompatíveis e repassa bytes PCM para `BroadcastManager.push_audio()`.

No player principal, o PCM é enfileirado antes do trabalho opcional de visualização e análise de silêncio. O visualizador reativo mantém todas as amostras, mas limita o cálculo/desenho a 30 atualizações por segundo. Os estilos reativos de onda/espectro também dispensam a decodificação integral da faixa em um segundo processo FFmpeg, usada apenas para desenhar a forma estática. Essa redução diminui trabalho concorrente no caminho da reprodução; ainda não prova que a carga da interface era a causa dos cortes.

## 3. Buffers por fonte

Em `radiostudio/services/broadcast.py`, cada fonte possui um `PCMBuffer`.

Constantes importantes:

```text
SAMPLE_RATE = 44100
CHANNELS = 2
FRAME_MS = 20
PCM_BYTES_PER_FRAME = 3528 bytes
```

Cálculo aproximado:

```text
44100 samples/s × 2 canais × 2 bytes × 0,020 s = 3528 bytes/frame
```

Cada `PCMBuffer` mantém até aproximadamente 2 segundos de PCM antes de descartar os blocos mais antigos.

### Comportamento atual de `PCMBuffer.take()`

O mixer pede sempre exatamente `PCM_BYTES_PER_FRAME`. Uma fonte nova espera dois frames (40 ms) antes de começar a ser consumida. Se ela ficar abaixo do frame pedido depois de iniciar, `take()` completa o restante com:

```python
b"\0"
```

Portanto, uma entrega irregular do Qt ainda pode produzir um frame parcialmente real e parcialmente silencioso. Aquecimento, underruns e bytes preenchidos com zero ficam contados para diagnóstico.

Depois de um underrun, a fonte permanece armada e volta a consumir assim que chegarem novos samples; ela não espera novamente os 40 ms completos. Isso evita estender uma pequena falha de temporização com silêncio de rebufferização.

Isso é especialmente relevante para investigar cliques, pequenas descontinuidades e microengasgos.

## 4. Mixer

A interface não usa diretamente o `BroadcastManager` básico. `radiostudio/ui/_shared.py` importa o `BroadcastManager` de:

```text
radiostudio/services/broadcast_quality.py
```

Esse manager herda a implementação base e sobrescreve `_mix_loop()`.

A cada 20 ms:

1. copia a lista atual de fontes;
2. pede um frame a cada `PCMBuffer`;
3. converte os bytes para amostras `int16`;
4. multiplica cada fonte pelo ganho correspondente;
5. soma em acumuladores inteiros Python;
6. aplica um limiter de pico;
7. converte o resultado para PCM `int16`;
8. envia o mesmo frame para todas as saídas de encoder.

O limiter atual deixa cerca de 2% de headroom antes do limite de `int16` e possui ataque rápido + release aproximado de 300 ms.

Isso evita hard clipping bruto em picos de crossfade/carts/entradas, mas não é um processador broadcast completo, não mede LUFS e não é true-peak/lookahead.

## 5. Fila entre mixer e encoder

Cada `EncoderOutput` mantém uma fila de PCM. A saída só aceita frames quando o encoder está pronto (`Ativo`); durante conexão e reconexão, frames novos são descartados para não transmitir backlog antigo quando o destino voltar:

```python
queue.Queue(maxsize=120)
```

Como o mixer gera um frame a cada 20 ms, a capacidade nominal corresponde a:

```text
120 × 20 ms = 2400 ms
```

ou aproximadamente **2,4 segundos de PCM**.

Quando a fila está cheia durante operação ativa, `feed()` remove um item antigo e tenta inserir o novo. Se um consumidor retirar o item ao mesmo tempo, `feed()` tenta inserir o frame atual novamente; descartes reais ficam contados. Ao trocar de faixa ou buscar, o PCM anterior é limpo; a fonte principal preserva o estado de aquecimento para não reinserir o silêncio inicial de 40 ms em cada troca. Na promoção do Mix, mantém o buffer da música que está entrando e descarta o tail da música que saiu.

Consequência: se o encoder ou a rede ficarem mais lentos do que o mixer por tempo suficiente, o programa pode começar a **descartar áudio antigo** em vez de aplicar backpressure ao produtor.

Isso é uma hipótese importante para saltos ou recuperação brusca após lag.

## 6. Encoder MP3

`EncoderOutput._run()` abre FFmpeg obtido por `imageio_ffmpeg`. Como o formato, a taxa e os canais do PCM de entrada são fixos e conhecidos, o encoder limita a análise de entrada com `-probesize 32 -analyzeduration 0`. Sem isso, o FFmpeg mantinha um atraso inicial observável enquanto analisava o pipe PCM.

Entrada do FFmpeg:

```text
-probesize 32
-analyzeduration 0
-f s16le
-ar 44100
-ac 2
-i pipe:0
```

Saída:

```text
-c:a libmp3lame
-b:a <bitrate>k
-id3v2_version 0
-write_xing 0
-f mp3
pipe:1
```

Em termos práticos:

```text
PCM int16 -> libmp3lame -> MP3 CBR
```

Novos perfis usam 192 kbps por padrão; o usuário ainda pode escolher 32–320 kbps. Há níveis de complexidade de 0 a 9 para `libmp3lame`; níveis menores priorizam qualidade e usam mais CPU.

### Re-encoding de material já comprimido

Se a música de origem for MP3:

```text
MP3 original
  ↓ decode Qt
PCM
  ↓ encode libmp3lame
MP3 do stream
```

Isso é uma **segunda compressão com perdas**. Em 128 kbps, artefatos perceptivos em pratos, sibilância, reverbs e texturas complexas são plausíveis.

## 7. Leitura e publicação do MP3 codificado

Uma thread lê a saída do FFmpeg em blocos de até 1024 bytes e os coloca em uma fila MP3 limitada. Uma segunda thread publica esses blocos para o destino.

```python
process.stdout.read(1024) -> fila MP3 (limite aproximado de 2 s, ajustado ao bitrate) -> writer de rede
```

Cada bloco lido é enviado para o destino.

A 128 kbps, 1024 bytes equivalem aproximadamente a:

```text
1024 × 8 / 128000 ≈ 0,064 s
```

Isso não significa necessariamente 64 ms adicionais fixos de latência, porque pipes e buffers do sistema podem retornar de maneiras diferentes. A fila limita o atraso acumulado; se lotar, a saída reconecta e descarta o backlog em vez de transmitir áudio cada vez mais atrasado.

## 8. Icecast

Para Icecast, `RemoteSink.connect()` cria um socket TCP e envia uma requisição HTTP `PUT` para o mount configurado.

Cabeçalhos relevantes incluem:

```text
Content-Type: audio/mpeg
Expect: 100-continue
Connection: keep-alive
Authorization: Basic ...
```

Após `100 Continue` (ou uma resposta de sucesso direta), os blocos MP3 são enviados sem enquadramento HTTP adicional. Isso segue o comportamento do muxer Icecast do FFmpeg (`chunked_post=0`) e mantém compatibilidade com Icecast 2.4.x, que não aceita corpo PUT em chunked encoding. A documentação do Icecast registra suporte a chunked PUT como adicionado na série 2.5.

O socket possui timeout. Se `sendall()` ficar bloqueado por rede/servidor lento, a thread de escrita para rede pode atrasar; a leitura do FFmpeg continua até a fila MP3 se esgotar. Se a fila lotar, a saída é reconectada.

## 9. SHOUTcast

SHOUTcast usa um handshake distinto, mas reutiliza o mesmo encoder MP3 e o mesmo `EncoderOutput`.

Por isso, qualquer defeito que já exista no PCM, mixer ou libmp3lame tende a aparecer tanto em Icecast quanto em SHOUTcast.

## 10. Separação das áreas de responsabilidade

```text
Qt Multimedia
  = playback + decode para PCM

AudioDecodeFallback / FFmpeg
  = decodificação alternativa para WAV temporário quando Qt rejeita a fonte

RadioStudio / Python
  = roteamento, ganhos, buffers, mixagem, limiter e timing

FFmpeg/libmp3lame
  = encode PCM -> MP3 para transmissão

RemoteSink
  = protocolo/socket para Icecast ou SHOUTcast

Icecast/SHOUTcast
  = recebe stream codificado e distribui aos ouvintes
```

Essa separação é essencial para o diagnóstico: antes de culpar o servidor, é necessário descobrir se o artefato já existe no MP3 produzido localmente pelo RadioStudio.
