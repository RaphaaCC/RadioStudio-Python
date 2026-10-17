# 03 — Plano de diagnóstico controlado

Objetivo: descobrir **em qual ponto exato do pipeline** aparecem o swishing e as travadas, sem partir direto para uma reescrita grande.

## Princípio

O teste deve comparar o mesmo trecho de áudio em pontos diferentes:

```text
fonte original
  ↓
PCM pré-encoder
  ↓
MP3 pós-FFmpeg / pré-rede
  ↓
stream recebido do Icecast
```

O primeiro ponto em que o defeito surgir indica a área responsável.

---

## Teste 1 — matriz de bitrate

Usar exatamente a mesma música, mesmo servidor e mesma rede.

Testar:

```text
128 kbps
192 kbps
256 kbps
320 kbps
```

Preferir um trecho com:

- pratos/hi-hat;
- voz com bastante sibilância;
- reverberação;
- passagem densa em agudos.

### Interpretação

Se o swishing cair progressivamente quando o bitrate sobe, isso aponta fortemente para **artefato de encoding/re-encoding MP3**.

Se o ruído permanecer praticamente idêntico em 320 kbps, investigar mixer, formato PCM, clipping/modulação ou outra etapa.

---

## Teste 2 — fonte lossless vs fonte MP3

Usar a mesma master, se possível, em:

```text
WAV/FLAC
MP3 320
MP3 128 ou 192
```

Transmitir todos com a mesma configuração de saída.

### Interpretação

Se WAV/FLAC sair claramente mais limpo que MP3, a **segunda geração de compressão lossy** está contribuindo.

Se todos apresentarem o mesmo defeito, a causa pode estar depois da decodificação.

---

## Teste 3 — outro encoder no mesmo Icecast

Condições devem ser mantidas:

- mesmo computador, se possível;
- mesma conexão;
- mesmo servidor Icecast;
- mesmo mount ou mount equivalente;
- mesmo arquivo;
- bitrate equivalente;
- mesmo codec.

Comparar RadioStudio com um encoder conhecido, por exemplo BUTT ou RadioBOSS.

### Interpretação

Se outro encoder ficar limpo e o RadioStudio não, o Icecast deixa de ser o principal suspeito.

Se ambos apresentarem lag/ruído semelhante, investigar servidor, host, rota e configuração do Icecast.

---

## Teste 4 — servidor embutido vs Icecast remoto

O RadioStudio possui servidor MP3 embutido que recebe o mesmo PCM do mixer e usa o mesmo `EncoderOutput`/FFmpeg, mas não passa pelo `RemoteSink` remoto.

Comparar:

```text
RadioStudio -> servidor embutido -> player local/LAN
RadioStudio -> Icecast remoto -> player
```

### Interpretação

Se o swishing existir nos dois, a causa provavelmente está **antes da rede**.

Se o servidor embutido estiver limpo e o Icecast remoto travar, investigar principalmente transporte/socket/rede/servidor.

---

## Teste 5 — captura imediatamente após o encoder

Criar temporariamente uma instrumentação de debug — sem manter isso no produto final — para salvar os bytes produzidos por `process.stdout` do FFmpeg em um `.mp3` local antes de `destination.publish()`.

Ponto de captura sugerido:

```text
EncoderOutput._read_encoded()
```

Comparar esse arquivo com o stream recebido do Icecast.

### Interpretação

```text
MP3 pré-rede já tem swishing
=> encoder/pipeline local

MP3 pré-rede limpo, Icecast recebido ruim
=> transporte/servidor/player/rede
```

Esse é um dos testes mais conclusivos.

---

## Teste 6 — captura PCM imediatamente antes do FFmpeg

Instrumentar temporariamente os frames que chegam em `EncoderOutput.feed()` ou saem do mixer para um arquivo RAW/WAV.

Formato atual esperado:

```text
s16le
44100 Hz
2 canais
```

Escutar/analisar esse PCM.

### Interpretação

```text
PCM limpo + MP3 ruim
=> encode MP3

PCM já com clicks/dropouts
=> mixer/buffering/captura Qt
```

---

## Teste 7 — detectar underruns reais do `PCMBuffer`

Adicionar contadores de diagnóstico temporários em `PCMBuffer.take()`:

```text
requested_bytes
available_bytes_before_take
zero_filled_bytes
source_id
monotonic_timestamp
```

É importante medir **quantas vezes zeros são inseridos enquanto uma faixa está tocando normalmente**.

Resultado ideal:

```text
zero_filled_bytes = 0
```

na fonte musical ativa, exceto em início/fim/transições deliberadas.

Se houver zero-fill recorrente durante playback estável, existe evidência objetiva de underrun/jitter entre Qt e o mixer.

---

## Teste 8 — medir ocupação da fila do encoder

Instrumentação temporária em `EncoderOutput.feed()`:

```text
queue.qsize()
dropped_frame_count
max_queue_size_seen
```

Registrar também timestamps.

### Sinais de problema

- fila subindo continuamente durante alguns segundos;
- atingir 120;
- `dropped_frame_count > 0`;
- crescimento correlacionado com o momento em que o ouvinte percebe lag.

Se isso ocorrer, existe gargalo downstream: encoder, pipe, socket ou servidor/rede.

---

## Teste 9 — separar CPU de rede

Executar um cenário de carga controlada:

### Cenário A

- máquina ociosa;
- servidor próximo/estável;
- somente música.

### Cenário B

- CPU ocupada;
- mesma transmissão.

### Cenário C

- upload de rede concorrente;
- CPU ociosa.

Observar underruns e queue depth.

Isso ajuda a identificar se o problema responde mais ao scheduler/CPU ou à rede.

---

## Teste 10 — transmissão sem fades/carts/microfone

Primeiro testar a cadeia mínima:

```text
1 música
sem crossfade
sem cart
sem entrada ao vivo
sem ducking
```

Depois ativar recursos um de cada vez.

Se o defeito só aparecer durante sobreposição de fontes, investigar mixer/limiter/ganhos.

---

## Teste 11 — conferir nível do barramento

Registrar:

- pico pré-limiter;
- ganho aplicado pelo limiter;
- pico pós-limiter.

Se o limiter permanecer reduzindo ganho com frequência mesmo em uma única faixa, investigar nível entregue pelo Qt e headroom.

Swishing puro normalmente não é causado por clipping, mas modulação excessiva pode produzir outro ruído que o usuário perceba como "fundo estranho".

---

## Teste 12 — verificar protocolo Icecast sem mudar o áudio

Depois de provar que o MP3 pré-rede está limpo, testar:

- servidor Icecast local na mesma máquina/LAN;
- servidor remoto atual;
- TLS ligado/desligado, quando aplicável;
- versão diferente de Icecast, se disponível.

Apenas chegar a esta etapa se o conteúdo pré-rede estiver comprovadamente limpo.

---

# Ordem recomendada

Executar nesta ordem para gastar menos tempo:

```text
1. Matriz 128/192/256/320
2. WAV/FLAC vs MP3
3. Servidor embutido vs Icecast remoto
4. Outro encoder no mesmo Icecast
5. Captura MP3 pré-rede
6. Captura PCM pré-encoder
7. Contador de zero-fill
8. Métricas da fila
9. CPU/rede
10. Processamento avançado
```

# Critério de diagnóstico fechado

Considerar a causa localizada quando houver um teste reproduzível em que:

1. o sinal esteja limpo no ponto N;
2. o defeito apareça no ponto N+1;
3. o resultado se repita pelo menos algumas vezes;
4. uma variável isolada modifique claramente o sintoma.

Evitar concluir apenas por sensação auditiva de um único teste.