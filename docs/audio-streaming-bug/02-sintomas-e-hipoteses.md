# 02 — Sintomas e hipóteses

Este documento separa os sintomas observados porque **"swishing"** e **"lag"** podem ter origens diferentes.

## Sintoma A — swishing / ruído aquoso / metálico

Descrição subjetiva relatada:

- um ruído de fundo persistente;
- parece "se mexer" junto com a música;
- caráter aquoso, metálico, plástico ou parecido com "shhh";
- incomoda mais do que um simples hiss estático.

Esse perfil é compatível com **artefatos de compressão perceptiva MP3**, especialmente em conteúdo já comprimido anteriormente.

### Hipótese A1 — dupla compressão MP3

**Confiança: alta**

Fluxo atual quando a fonte já é MP3:

```text
MP3 original -> decode Qt -> PCM -> encode libmp3lame -> MP3 do stream
```

Isso não é bit-perfect. O encoder de saída tenta novamente modelar e remover informação perceptualmente menos importante de um sinal que já passou por uma compressão com perdas.

Elementos normalmente sensíveis:

- pratos, hi-hats e cymbals;
- sibilância de voz;
- reverberação;
- ambiência estéreo;
- synth pads e texturas densas;
- passagens com muitos detalhes agudos.

Em 128 kbps estéreo, o risco de o efeito ficar audível cresce.

### Hipótese A2 — configuração simples demais do encoder

**Confiança: média/alta**

A chamada atual ao FFmpeg usa essencialmente `libmp3lame` + bitrate CBR escolhido no perfil. Não existe hoje uma camada explícita de preset de qualidade, política de joint stereo, lowpass controlado pelo aplicativo ou estratégia adaptativa de codec.

Isso não significa que o LAME esteja "errado" — ele já toma decisões internas razoáveis — mas o RadioStudio não possui ainda uma política de encoding pensada especificamente para música de rádio.

### Hipótese A3 — bitrate insuficiente para a fonte

**Confiança: alta quando o perfil está em 128 kbps**

128 kbps ainda é utilizável, mas em material complexo e principalmente após recompressão pode introduzir artefatos audíveis.

Se o swishing cair drasticamente em 192/256/320 kbps, isso é evidência forte de que o problema está na etapa de encode/re-encode.

### Hipótese A4 — limiter/mix causando "pumping" confundido com swishing

**Confiança: baixa/média**

O limiter atual opera por frame e ajusta ganho global. Em picos frequentes, ele pode gerar modulação de nível perceptível.

Entretanto, isso tende a soar mais como **pumping/respiração** do que como swishing MP3 clássico. Deve ser testado, mas não é a primeira suspeita.

---

## Sintoma B — microtravadas, lag, cortes ou pequenos engasgos

### Hipótese B1 — `PCMBuffer.take()` insere zeros quando faltam dados

**Confiança: média/alta**

O mixer consome um frame fixo a cada 20 ms. Se uma fonte não tiver bytes suficientes naquele instante, o `PCMBuffer.take()` completa o frame com zeros.

Isso é simples e determinístico, mas assume que a produção de PCM pelo Qt é suficientemente regular.

Se o `QAudioBufferOutput` entregar buffers com jitter de scheduling, o mixer pode consumir cedo demais e inserir silêncio mesmo que o áudio chegue poucos milissegundos depois.

Possíveis efeitos:

- clique curto;
- microdropout;
- sensação de "engolida";
- alteração momentânea no conteúdo antes do encoder;
- aumento de artefatos MP3 ao codificar uma descontinuidade.

### Hipótese B2 — fila do encoder acumula e descarta áudio

**Confiança: média**

`EncoderOutput.queue` possui 120 posições. Em frames de 20 ms, isso equivale a cerca de 2,4 segundos.

Quando a fila lota, o código remove o item mais antigo e insere o mais novo.

Se o encoder ou o envio remoto ficarem lentos:

```text
mixer continua produzindo
       ↓
fila cresce
       ↓
fila atinge limite
       ↓
frame antigo é descartado
       ↓
salto temporal no áudio
```

Isso pode explicar um stream que "atrasa um pouco e depois dá uma recuperada".

### Hipótese B3 — bloqueio em `sendall()` / instabilidade de rede

**Confiança: média**

A saída Icecast usa socket TCP e `sendall()`.

Se a rota até o servidor tiver jitter, upload saturado, perda de pacotes ou o servidor estiver demorando a consumir dados, `sendall()` pode bloquear.

Como a thread que lê `stdout` do FFmpeg é também a que publica no destino, um bloqueio de rede reduz a velocidade de drenagem do pipe do encoder.

Cadeia possível:

```text
rede lenta
  ↓
sendall demora
  ↓
stdout do FFmpeg é drenado mais devagar
  ↓
FFmpeg pode desacelerar
  ↓
fila PCM aumenta
  ↓
descarte de frames se atingir 120
```

### Hipótese B4 — scheduling e trabalho na thread Qt/Python

**Confiança: média/baixa até medir**

O mixer é uma thread Python que acorda em intervalos de 20 ms. Windows não é um sistema operacional hard real-time. GIL, carga de CPU, antivírus, outros threads e callbacks Qt podem introduzir jitter. O callback de áudio também atualiza o visualizador e, opcionalmente, analisa silêncio. O PCM agora é enfileirado antes dessas tarefas; a atualização reativa foi limitada a 30 Hz, e estilos reativos deixaram de iniciar uma segunda decodificação integral da música para desenhar a forma estática. Isso reduz contenção potencial, mas não confirma essa hipótese como causa.

O loop tenta compensar drift usando `time.monotonic()`, mas ainda depende do scheduler acordar próximo ao instante correto.

Isso pode ser irrelevante em máquinas tranquilas ou pode se tornar importante sob carga.

---

## Sintoma C — problema só aparece via Icecast, mas não na saída local

Isso **não prova** que o Icecast é culpado.

### Evidência de reconexão em loop (17/10/2026)

A captura enviada mostra o mesmo perfil Icecast alternando entre `WinError 10053` e `Servidor encerrou a conexão` a cada poucos segundos. O código anterior enviava HTTP `PUT` com `Transfer-Encoding: chunked`. A documentação oficial registra que Icecast 2.4.2 não aceitava chunked encoding em PUT; o suporte foi acrescentado na série 2.5. O muxer Icecast do FFmpeg usa `Expect: 100-continue` e desativa chunked (`chunked_post=0`).

Essa incompatibilidade é uma causa plausível e diretamente compatível com o ciclo mostrado, embora a versão do servidor do usuário não esteja confirmada. O RadioStudio agora usa `Expect: 100-continue` e envia MP3 bruto em HTTP PUT, sem chunk framing, seguindo a abordagem do FFmpeg. Referências: [Icecast 2.4.2](https://icecast.org/news/icecast-release-2_4_2/), [Icecast 2.5 beta2](https://icecast.org/news/icecast-release-2_5-beta2/) e [muxer Icecast do FFmpeg](https://ffmpeg.org/doxygen/8.1/icecast_8c_source.html).

A saída local escutada pelo usuário usa `QAudioOutput`, enquanto a transmissão percorre:

```text
QAudioBufferOutput -> mixer -> limiter -> FFmpeg -> MP3 -> socket
```

Logo, é perfeitamente possível o áudio local ficar limpo e o stream remoto apresentar artefatos criados depois da saída local.

## Sintoma D — problema aparece em Icecast e SHOUTcast

Se o mesmo swishing aparecer nos dois servidores com a mesma configuração, isso reforça a hipótese de origem **antes do protocolo**, porque ambos compartilham:

- PCM capturado do Qt;
- mixer;
- limiter;
- fila;
- FFmpeg/libmp3lame.

## Sintoma E — problema some ao usar outro encoder com o mesmo Icecast

Esse é um teste de alto valor diagnóstico.

Se RadioBOSS/BUTT/outro encoder transmitir o mesmo arquivo para o mesmo servidor, na mesma rede e bitrate semelhante, sem o defeito, a probabilidade de o problema principal estar no RadioStudio aumenta muito.

## Ranking provisório

### Para swishing

1. **Re-encoding MP3 + bitrate/codec** — principal suspeita.
2. Configuração do encoder/preset — suspeita secundária.
3. Modulação causada pelo processamento de ganho — possível, mas menos provável.
4. Icecast alterando o conteúdo — pouco provável em configuração comum, pois o servidor recebe o stream já codificado.

### Para lag/microcortes

1. Temporização/buffering PCM e zero-fill.
2. Fila de 120 frames + descarte quando cheia.
3. Bloqueio de rede/socket.
4. Scheduling do sistema/CPU.
5. Problema interno do Icecast ou host remoto.

## O que ainda NÃO está comprovado

Nenhuma das hipóteses acima deve ser tratada como causa confirmada sem teste controlado.

Ainda não foi capturado e comparado:

- PCM imediatamente antes do FFmpeg;
- MP3 imediatamente após o FFmpeg mas antes do socket;
- stream baixado do Icecast;
- o mesmo conteúdo enviado por outro encoder;
- comportamento em diferentes bitrates.

O próximo passo ideal é localizar o primeiro ponto da cadeia em que o defeito aparece.
