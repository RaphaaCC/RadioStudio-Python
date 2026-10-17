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

Ou seja, o Qt decodifica MP3/FLAC/WAV/etc. e entrega PCM para o RadioStudio.

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

### Comportamento importante de `PCMBuffer.take()`

O mixer pede sempre exatamente `PCM_BYTES_PER_FRAME`.

Se houver menos dados disponíveis do que o frame solicitado, `take()` completa o restante com:

```python
b"\0"
```

Portanto, uma entrega irregular do Qt pode potencialmente produzir um frame parcialmente real e parcialmente silencioso.

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

Cada `EncoderOutput` possui:

```python
queue.Queue(maxsize=120)
```

Como o mixer gera um frame a cada 20 ms, a capacidade nominal corresponde a:

```text
120 × 20 ms = 2400 ms
```

ou aproximadamente **2,4 segundos de PCM**.

Quando a fila está cheia, `feed()` remove um item antigo e tenta inserir o novo.

Consequência: se o encoder ou a rede ficarem mais lentos do que o mixer por tempo suficiente, o programa pode começar a **descartar áudio antigo** em vez de aplicar backpressure ao produtor.

Isso é uma hipótese importante para saltos ou recuperação brusca após lag.

## 6. Encoder MP3

`EncoderOutput._run()` abre FFmpeg obtido por `imageio_ffmpeg`.

Entrada do FFmpeg:

```text
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

O perfil novo de transmissão usa 128 kbps por padrão, embora o usuário possa configurar outro valor.

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

## 7. Leitura do MP3 codificado

Uma thread separada executa aproximadamente:

```python
process.stdout.read(4096)
```

Cada bloco lido é enviado para o destino.

A 128 kbps, 4096 bytes equivalem aproximadamente a:

```text
4096 × 8 / 128000 ≈ 0,256 s
```

Isso não significa necessariamente 256 ms adicionais fixos de latência, porque pipes e buffers do sistema podem retornar de maneiras diferentes, mas mostra que a publicação trabalha em blocos relativamente grandes de saída comprimida.

## 8. Icecast

Para Icecast, `RemoteSink.connect()` cria um socket TCP e envia uma requisição HTTP `PUT` para o mount configurado.

Cabeçalhos relevantes incluem:

```text
Content-Type: audio/mpeg
Transfer-Encoding: chunked
Connection: keep-alive
Authorization: Basic ...
```

Depois da autenticação, cada bloco MP3 é publicado usando HTTP chunked encoding:

```text
<tamanho hexadecimal>\r\n
<dados MP3>\r\n
```

O socket possui timeout. Se `sendall()` ficar bloqueado por rede/servidor lento, a thread de leitura/publicação do encoder também pode atrasar.

## 9. SHOUTcast

SHOUTcast usa um handshake distinto, mas reutiliza o mesmo encoder MP3 e o mesmo `EncoderOutput`.

Por isso, qualquer defeito que já exista no PCM, mixer ou libmp3lame tende a aparecer tanto em Icecast quanto em SHOUTcast.

## 10. Separação das áreas de responsabilidade

```text
Qt Multimedia
  = playback + decode para PCM

RadioStudio / Python
  = roteamento, ganhos, buffers, mixagem, limiter e timing

FFmpeg/libmp3lame
  = encode PCM -> MP3

RemoteSink
  = protocolo/socket para Icecast ou SHOUTcast

Icecast/SHOUTcast
  = recebe stream codificado e distribui aos ouvintes
```

Essa separação é essencial para o diagnóstico: antes de culpar o servidor, é necessário descobrir se o artefato já existe no MP3 produzido localmente pelo RadioStudio.