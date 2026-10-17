# 04 — Opções de correção

Este documento não implementa mudanças. Ele organiza caminhos possíveis para uma futura sessão de desenvolvimento.

## Prioridade 1 — confirmar o ponto de origem antes de reescrever

Antes de alterar arquitetura, executar o plano de `03-plano-de-diagnostico.md`.

O principal risco seria tentar "consertar o Icecast" quando o defeito já estiver no MP3 produzido localmente, ou trocar o encoder quando o problema real for underrun de PCM.

---

## Prioridade 2 — melhorar a política de encoding MP3

### Objetivo

Reduzir artefatos perceptivos sem redesenhar o playback.

### Mudanças possíveis

- elevar o bitrate padrão para 192 kbps ou mais;
- oferecer presets explícitos de qualidade;
- revisar parâmetros do `libmp3lame` usados pelo FFmpeg;
- expor uma opção de qualidade mais alta para transmissão musical;
- manter compatibilidade com Icecast/SHOUTcast e players existentes.

### Benefício esperado

Maior impacto imediato no swishing quando a causa for compressão/recompressão.

### Risco

Maior uso de banda e possível aumento de CPU dependendo das opções escolhidas.

---

## Prioridade 3 — impedir underrun artificial no mixer

### Problema atual

`PCMBuffer.take()` preenche com zeros quando não há bytes suficientes no instante em que o mixer pede o frame.

### Caminhos possíveis

1. **Prebuffer curto antes de começar a consumir uma fonte.**
2. **Jitter buffer** com alvo mínimo de alguns frames.
3. Distinguir "fonte atrasada" de "fonte realmente terminou".
4. Evitar zero-fill imediato para uma fonte ativa quando os dados estiverem apenas alguns milissegundos atrasados.
5. Manter timing global sem deixar uma fonte lenta bloquear todas as demais.

### Cuidados

- não aumentar latência indefinidamente;
- não quebrar crossfades;
- não manter áudio velho após seek/stop/troca de faixa;
- limpar buffers corretamente ao remover uma fonte;
- tratar início e fim de playback de forma diferente de jitter durante playback estável.

---

## Prioridade 4 — observabilidade de fila e drops

Hoje o `EncoderOutput.feed()` descarta o frame mais antigo quando a fila está cheia.

Isso protege memória, mas esconde um problema importante.

Adicionar métricas/eventos como:

```text
encoder_queue_depth
encoder_queue_high_watermark
encoder_dropped_frames
source_pcm_underruns
socket_publish_duration_ms
```

Essas métricas podem ficar apenas em log técnico, sem poluir a UI.

### Benefício

Permite distinguir rapidamente:

```text
problema de produção PCM
vs
problema de encoder
vs
problema de rede
```

---

## Prioridade 5 — desacoplar melhor encoder e rede

Hoje a thread que lê o `stdout` do FFmpeg também chama `destination.publish()`.

Se o socket bloquear, a leitura do encoder também para temporariamente.

### Arquitetura possível

```text
Mixer
  ↓
Fila PCM
  ↓
FFmpeg
  ↓
Fila MP3
  ↓
Writer de rede
  ↓
Icecast
```

Com filas separadas, o programa pode medir exatamente onde aparece congestionamento e aplicar política de recuperação específica.

### Decisões necessárias

Quando a rede ficar lenta, escolher uma política clara:

- tolerar atraso até um limite;
- reconectar;
- descartar áudio de forma controlada;
- nunca deixar latência crescer indefinidamente.

Para rádio ao vivo, geralmente é melhor preservar "tempo real" do que transmitir conteúdo vários segundos atrasado.

---

## Prioridade 6 — considerar mixer interno em float

O barramento atual recebe `int16`, soma em inteiros Python, aplica ganho/limiter e volta para `int16`.

Uma evolução possível:

```text
PCM de entrada -> float32 interno -> mix/processamento -> dither/conversão final -> int16/encoder
```

### Benefícios potenciais

- mais headroom matemático;
- ganho/fades mais naturais para processamento futuro;
- menor risco de quantizações repetidas durante DSP;
- base melhor para compressor/AGC/EQ futuros.

### Limite

Isso **não resolve por si só swishing MP3**. Não deve ser vendido como correção principal se a captura PCM já estiver limpa.

---

## Prioridade 7 — AAC-LC opcional para Icecast

Para servidores/players compatíveis, AAC-LC pode oferecer qualidade perceptiva melhor do que MP3 em alguns bitrates.

### Possível estratégia

- manter MP3 como padrão por compatibilidade;
- permitir AAC-LC em perfis Icecast;
- validar `Content-Type`, extensão/mount e suporte dos players.

### Risco

Compatibilidade de ecossistema. SHOUTcast/players antigos podem esperar MP3.

---

## Prioridade 8 — passthrough de áudio comprimido

Ideia avançada: quando houver apenas uma faixa MP3 tocando, sem mix, mic, cart, ganho variável ou DSP, enviar frames MP3 sem decodificar/re-encodar.

### Vantagem

Evita uma geração de compressão com perdas:

```text
MP3 -> Icecast
```

em vez de:

```text
MP3 -> PCM -> MP3
```

### Complexidade alta

O RadioStudio precisaria alternar entre passthrough e PCM precisamente antes de:

- fade;
- crossfade;
- cart;
- microfone;
- ducking;
- seek;
- mudança de bitrate/codec.

Também seria necessário preservar timing, headers e fronteiras de frame MP3.

### Recomendação

Não começar por aqui. É uma otimização arquitetural avançada depois de resolver o caminho PCM normal.

---

# Estratégia recomendada de implementação

Uma sequência conservadora seria:

```text
Fase 1
- instrumentar underrun/fila
- capturar PCM e MP3 pré-rede
- confirmar causa

Fase 2
- melhorar encoding/padrões de bitrate
- corrigir underruns/jitter se comprovados

Fase 3
- separar encoder da escrita de rede
- melhorar política de congestionamento

Fase 4
- mixer float/AAC opcional

Fase 5
- avaliar passthrough, somente se ainda fizer sentido
```

# Critérios de aceitação

Uma correção de qualidade não deve ser aceita apenas porque "parece melhor".

Idealmente validar:

- ausência de swishing claramente superior em A/B com a mesma fonte;
- nenhum zero-fill durante playback estável;
- nenhum frame descartado em condições normais;
- fila do encoder estável;
- fades/mix continuam corretos;
- mic/carts/ducking continuam funcionando;
- Icecast e SHOUTcast continuam conectando;
- servidor embutido continua funcional;
- CPU e memória não aumentam de forma excessiva;
- testes automatizados e smoke test continuam verdes.

# Evitar

- mascarar o defeito com filtros de EQ;
- aumentar buffer indefinidamente para esconder lag;
- trocar codec sem provar onde o problema nasce;
- remover o limiter sem teste de clipping;
- alterar simultaneamente mixer, encoder e protocolo, porque isso dificulta saber qual mudança realmente resolveu o problema.