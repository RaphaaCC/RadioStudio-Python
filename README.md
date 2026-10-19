# 🎙️ RadioStudio Python Sirius

**v1.0.1 Stable · Windows**

Automação de áudio para rádio com playlist, AutoDJ, entradas ao vivo e transmissão. Projeto independente, com interface em português, inspirado em fluxos descritos no manual do RadioBOSS 7.2.

## Começar

No Windows, abra **Iniciar RadioStudio Python.bat**. Na primeira execução, o programa prepara o ambiente e instala as dependências necessárias.

## Recursos

| Área | O que oferece |
| --- | --- |
| **Playlist e reprodução** | Adicione arquivos ou pastas, reorganize a fila, salve listas M3U e controle a reprodução local. |
| **Mixagem** | Configure fades e sobreposição de faixas; monitore o áudio e as capas. |
| **AutoDJ** | Indexe bibliotecas, programe blocos e filtros, evite repetições e consulte o histórico. |
| **Entradas e ao vivo** | Use um microfone, até cinco auxiliares, Cart wall com 25 botões e agendamento de faixas. |
| **Transmissão** | Transmita pelo servidor embutido, Icecast ou SHOUTcast. Acompanhe o estado das saídas e a contagem de ouvintes quando disponível. |
| **Estação** | Configure metadados, título no ar e Rich Presence do Discord. |

## Configurar transmissão

- **Servidor embutido:** na aba **📡 Transmissão**, ative o servidor e clique em **Iniciar transmissão**. A aba mostra o endereço; o stream usa `/live.mp3` e o estado fica disponível em `/status.json`.
- **Icecast:** crie um perfil com servidor, porta, mount e usuário de origem.
- **SHOUTcast:** escolha **SHOUTcast (fonte v1/v2 compatível)** e informe a porta de origem.

As senhas de transmissão ficam no **Gerenciador de Credenciais do Windows**, não no arquivo de configurações. O encoder transmite em MP3 estéreo a 44,1 kHz; novos perfis usam 192 kbps por padrão.

## Arquivos e configurações

- **Setup:** `%APPDATA%\RadioStudioPython`
- **Portable:** pasta `RadioStudioPythonData` junto ao executável. Se não puder gravar nela, o programa usa `%APPDATA%\RadioStudioPython`.

Configurações, bibliotecas e histórico ficam no perfil do usuário. Senhas são guardadas separadamente no Gerenciador de Credenciais do Windows.

## Manual e ajuda

Consulte o [Manual do usuário](radiostudio/assets/manual_usuario.pdf) ou abra **Ajuda > Manual do usuário** no programa. O histórico de eventos e o log técnico ficam na pasta de dados do aplicativo.

## Limitações conhecidas

- A interface está disponível em português.
- A transmissão codifica em MP3. O protocolo SHOUTcast nativo v2 ainda não é implementado; o RadioStudio usa fonte v1 compatível com servidores DNAS v2.
- A reprodução de cada formato depende do codec disponível no Windows e no FFmpeg incluído.
- A conexão com servidores externos depende da configuração do serviço e deve ser validada com o servidor de destino.

## Gerar executáveis para Windows

No ambiente do projeto, instale as ferramentas de build e execute o script no PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\packaging\build_release.ps1
```

O script gera as versões **Portable** e **Setup**. O Setup preserva as configurações e o histórico ao desinstalar; os executáveis não têm assinatura digital.

---

Para relatar um problema, escreva para [raphael@raemi.xyz](mailto:raphael@raemi.xyz).
