\## RadioStudio Python Sirius v1.0.2 Stable



\### Novidades



\- \*\*Biblioteca compartilhada:\*\* catálogo unificado das pastas indexadas, com pesquisa, filtros, ordenação, coleções manuais e inteligentes, etiquetas e grupos de rotação.

\- \*\*Editor de metadados:\*\* edição individual ou em lote, com opção de salvar nos arquivos ou em dados internos do RadioStudio. Inclui capas e tags personalizadas, preservação do original em caso de falha e possibilidade de desfazer alterações.

\- \*\*AutoDJ integrado à Biblioteca:\*\* sem blocos de programação ativos, considera o catálogo inteiro. Cada bloco pode limitar a seleção a pastas indexadas, coleções ou grupos, além de aplicar filtros e pesos. Faixas de fontes sobrepostas não são duplicadas.

\- \*\*API REST local:\*\* endpoints versionados para consultar status e fila, controlar reprodução e reorganizar a fila. A API é opcional, exige autenticação e permite configurar origens CORS. Inclui documentação e especificação OpenAPI.

\- \*\*Gravação:\*\* grava o Master Ao Vivo em WAV ou MP3, mesmo sem transmissão ativa. Oferece controles manuais, início automático junto com a transmissão e agendas únicas ou semanais.

\- \*\*Agendador aprimorado:\*\* melhor suporte a dias da semana, eventos únicos, horários que atravessam a meia-noite e validação das agendas.



\### Melhorias



\- Separação dos controles \*\*Master Ao Vivo\*\* e \*\*Monitoramento\*\*: o Master controla o áudio enviado às saídas; o Monitoramento ajusta somente a escuta local.

\- Controles de microfone mais acessíveis na área de reprodução.

\- Opção para disponibilizar a capa da faixa pelo servidor embutido. O envio de imagens por servidores externos depende do suporte e da configuração de cada servidor.

\- Melhor tratamento de conexões Icecast e SHOUTcast, incluindo timeouts, tentativas de reconexão, cancelamento e encerramento de conexões.

\- Falhas no servidor embutido ou em um destino externo ficam mais isoladas para evitar afetar os demais destinos.

\- Atualizações na reprodução, mixagem, fades, AutoDJ, Cart Wall e integração de metadados com a fila, transmissão e Discord.

\- Interface reorganizada, com novos ícones SVG e melhorias nas configurações e na janela Sobre.

\- Backups manuais e periódicos, importação de configurações e ferramenta para reparar o banco de dados.

\- Catálogos de tradução revisados para português (Brasil), inglês (EUA), espanhol (México), coreano, japonês, chinês tradicional e chinês simplificado. A troca de idioma pode reiniciar o programa para aplicar a interface.



\### Correções



\- Correções de estabilidade na transmissão, reprodução, fila do AutoDJ, Cart Wall, gravação e agendador.

\- Melhor tratamento de credenciais ausentes, portas ocupadas, conexões interrompidas e falhas de encoder ou gravação.

\- Ajustes na atualização e organização dos metadados e da Biblioteca.



\### Licença e distribuição



\- O projeto passou a ser \*\*source-available\*\*, sob a licença PolyForm Noncommercial 1.0.0. O uso comercial exige uma licença separada.

\- Os nomes dos executáveis identificam a arquitetura x64 do programa distribuído.

