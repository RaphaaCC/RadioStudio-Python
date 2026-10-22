"""Generate the Portuguese RadioStudio user manual PDF.

Run with Python that has ReportLab installed:
    python docs/generate_user_manual.py
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
)
from reportlab.platypus.tableofcontents import TableOfContents


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "radiostudio" / "assets"
LOGO_FILE = ASSET_DIR / "logo-about-window.png"
OUTPUT_FILE = ASSET_DIR / "manual_usuario.pdf"
VERSION = "v1.0.2 Beta"
PAGE_WIDTH, PAGE_HEIGHT = A4

NAVY = colors.HexColor("#071A3D")
BLUE = colors.HexColor("#0068D9")
CYAN = colors.HexColor("#00BCEB")
GOLD = colors.HexColor("#FFC400")
INK = colors.HexColor("#253449")
MUTED = colors.HexColor("#58677B")
PALE = colors.HexColor("#EAF6FC")
PALE_GOLD = colors.HexColor("#FFF8DF")
RULE = colors.HexColor("#D7E0E9")


def register_fonts() -> None:
    font_dir = Path(__import__("reportlab").__file__).parent / "fonts"
    pdfmetrics.registerFont(TTFont("Vera", str(font_dir / "Vera.ttf")))
    pdfmetrics.registerFont(TTFont("Vera-Bold", str(font_dir / "VeraBd.ttf")))
    pdfmetrics.registerFont(TTFont("Vera-Italic", str(font_dir / "VeraIt.ttf")))
    pdfmetrics.registerFontFamily(
        "Vera", normal="Vera", bold="Vera-Bold", italic="Vera-Italic",
        boldItalic="Vera-Bold",
    )


register_fonts()
STYLES = getSampleStyleSheet()
STYLES.add(ParagraphStyle(
    name="ManualBody", fontName="Vera", fontSize=9.1, leading=13.5,
    textColor=INK, spaceAfter=6, alignment=TA_LEFT, splitLongWords=True,
))
STYLES.add(ParagraphStyle(
    name="ManualChapter", fontName="Vera-Bold", fontSize=17.5, leading=22,
    textColor=NAVY, spaceBefore=3, spaceAfter=12, keepWithNext=True,
))
STYLES.add(ParagraphStyle(
    name="ManualFrontTitle", parent=STYLES["ManualChapter"],
))
STYLES.add(ParagraphStyle(
    name="ManualSection", fontName="Vera-Bold", fontSize=11.7, leading=15,
    textColor=BLUE, spaceBefore=8, spaceAfter=5, keepWithNext=True,
))
STYLES.add(ParagraphStyle(
    name="ManualBullet", parent=STYLES["ManualBody"], leftIndent=13,
    firstLineIndent=-9, bulletIndent=0, spaceAfter=4,
))
STYLES.add(ParagraphStyle(
    name="ManualSmall", fontName="Vera", fontSize=7.8, leading=11,
    textColor=MUTED, spaceAfter=4,
))
STYLES.add(ParagraphStyle(
    name="ManualStep", parent=STYLES["ManualBody"], leftIndent=8,
    spaceAfter=5,
))
STYLES.add(ParagraphStyle(
    name="ManualTOC1", fontName="Vera-Bold", fontSize=9.3, leading=13,
    leftIndent=0, firstLineIndent=0, textColor=NAVY, spaceBefore=4,
))
STYLES.add(ParagraphStyle(
    name="ManualTOC2", fontName="Vera", fontSize=8.5, leading=11.5,
    leftIndent=15, firstLineIndent=0, textColor=INK, spaceBefore=1,
))


def paragraph(text: str, style: str = "ManualBody") -> Paragraph:
    return Paragraph(escape(text).replace("\n", "<br/>"), STYLES[style])


def bullets(items: list[str]) -> list[Paragraph]:
    return [
        Paragraph(
            f'<font color="#0068D9">•</font> {escape(item)}',
            STYLES["ManualBullet"],
        )
        for item in items
    ]


def steps(items: list[str]) -> list[Paragraph]:
    return [
        Paragraph(
            f'<font color="#0068D9"><b>{index}.</b></font> {escape(item)}',
            STYLES["ManualStep"],
        )
        for index, item in enumerate(items, 1)
    ]


def callout(text: str, warning: bool = False) -> Table:
    fill = PALE_GOLD if warning else PALE
    accent = GOLD if warning else CYAN
    cell = Paragraph(escape(text), STYLES["ManualBody"])
    table = Table([["", cell]], colWidths=[3 * mm, PAGE_WIDTH - 51 * mm])
    table.setStyle([
        ("BACKGROUND", (0, 0), (0, 0), accent),
        ("BACKGROUND", (1, 0), (1, 0), fill),
        ("BOX", (0, 0), (-1, -1), 0.5, fill),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("RIGHTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 9),
        ("RIGHTPADDING", (1, 0), (1, 0), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ])
    return table


class CoverPage(Flowable):
    def __init__(self, logo_path: Path):
        super().__init__()
        self.logo_path = logo_path

    def wrap(self, width, height):
        return width, height

    def draw(self):
        canvas = self.canv
        canvas.saveState()
        canvas.setFillColor(NAVY)
        canvas.rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#0B2D62"))
        canvas.circle(PAGE_WIDTH + 5, PAGE_HEIGHT - 98, 118, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#0A2451"))
        canvas.circle(16, 85, 150, fill=1, stroke=0)
        for index, height in enumerate((30, 54, 43, 74, 48, 66, 37)):
            canvas.setFillColor(CYAN if index % 2 == 0 else BLUE)
            canvas.roundRect(
                PAGE_WIDTH - 48 - index * 15, 67, 7, height, 3,
                fill=1, stroke=0,
            )
        canvas.setFillColor(GOLD)
        canvas.rect(0, 0, PAGE_WIDTH, 7, fill=1, stroke=0)

        card_x, card_y = 30 * mm, PAGE_HEIGHT - 111 * mm
        card_w, card_h = PAGE_WIDTH - 60 * mm, 48 * mm
        canvas.setFillColor(colors.white)
        canvas.roundRect(card_x, card_y, card_w, card_h, 6 * mm, fill=1, stroke=0)
        from reportlab.lib.utils import ImageReader

        image = ImageReader(str(self.logo_path))
        iw, ih = image.getSize()
        scale = min((card_w - 20 * mm) / iw, (card_h - 9 * mm) / ih)
        draw_w, draw_h = iw * scale, ih * scale
        canvas.drawImage(
            image, card_x + (card_w - draw_w) / 2,
            card_y + (card_h - draw_h) / 2, draw_w, draw_h,
            preserveAspectRatio=True, mask="auto",
        )

        canvas.setFillColor(colors.white)
        canvas.setFont("Vera-Bold", 14)
        canvas.drawString(30 * mm, PAGE_HEIGHT - 132 * mm, "GUIA PRÁTICO DE OPERAÇÃO")
        canvas.setFillColor(CYAN)
        canvas.setFont("Vera-Bold", 29)
        canvas.drawString(30 * mm, PAGE_HEIGHT - 149 * mm, "Manual do usuário")
        canvas.setFillColor(colors.white)
        canvas.setFont("Vera-Bold", 15)
        canvas.drawString(30 * mm, PAGE_HEIGHT - 166 * mm, "RadioStudio Python Sirius")
        canvas.setFillColor(colors.HexColor("#D1E6F8"))
        canvas.setFont("Vera", 11)
        canvas.drawString(30 * mm, PAGE_HEIGHT - 175 * mm, VERSION)

        canvas.setStrokeColor(colors.HexColor("#31527A"))
        canvas.line(30 * mm, PAGE_HEIGHT - 193 * mm, PAGE_WIDTH - 30 * mm, PAGE_HEIGHT - 193 * mm)
        canvas.setFillColor(colors.white)
        canvas.setFont("Vera", 10)
        canvas.drawString(30 * mm, PAGE_HEIGHT - 205 * mm,
                          "Playlist, AutoDJ, gravação e transmissão")
        canvas.setFillColor(colors.HexColor("#B9CBE0"))
        canvas.setFont("Vera", 8.5)
        canvas.drawString(30 * mm, 32 * mm, "Edição em português • Raemi Labs • 2026")
        canvas.restoreState()


class ManualDocTemplate(BaseDocTemplate):
    def __init__(self, filename: Path):
        super().__init__(
            str(filename), pagesize=A4, title="Manual do usuário - RadioStudio Python Sirius",
            author="Raemi Labs", subject=f"Guia do usuário {VERSION}",
            leftMargin=24 * mm, rightMargin=24 * mm,
            topMargin=23 * mm, bottomMargin=19 * mm,
        )
        cover_frame = Frame(
            0, 0, PAGE_WIDTH, PAGE_HEIGHT, id="cover",
            leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        body_frame = Frame(
            24 * mm, 19 * mm, PAGE_WIDTH - 48 * mm, PAGE_HEIGHT - 42 * mm,
            id="body", leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        )
        self.addPageTemplates([
            PageTemplate(id="Cover", frames=[cover_frame]),
            PageTemplate(id="Body", frames=[body_frame], onPage=self.draw_body_page),
        ])
        self._heading_counter = 0

    def beforeDocument(self):
        self._heading_counter = 0

    def draw_body_page(self, canvas, doc):
        canvas.saveState()
        canvas.setFillColor(CYAN)
        canvas.rect(0, PAGE_HEIGHT - 8, PAGE_WIDTH, 8, fill=1, stroke=0)
        canvas.setFillColor(NAVY)
        canvas.setFont("Vera-Bold", 8)
        canvas.drawString(24 * mm, PAGE_HEIGHT - 15 * mm, "RADIOSTUDIO PYTHON SIRIUS")
        canvas.setFillColor(MUTED)
        canvas.setFont("Vera", 8)
        canvas.drawRightString(PAGE_WIDTH - 24 * mm, PAGE_HEIGHT - 15 * mm, VERSION)
        canvas.setStrokeColor(RULE)
        canvas.line(24 * mm, PAGE_HEIGHT - 18 * mm, PAGE_WIDTH - 24 * mm, PAGE_HEIGHT - 18 * mm)
        canvas.line(24 * mm, 14 * mm, PAGE_WIDTH - 24 * mm, 14 * mm)
        canvas.setFont("Vera", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(24 * mm, 9 * mm, "Manual do usuário • Raemi Labs")
        canvas.drawRightString(PAGE_WIDTH - 24 * mm, 9 * mm, f"Página {doc.page}")
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if not isinstance(flowable, Paragraph):
            return
        style = flowable.style.name
        if style not in ("ManualChapter", "ManualSection"):
            return
        level = 0 if style == "ManualChapter" else 1
        title = flowable.getPlainText()
        key = f"manual-heading-{self._heading_counter}"
        self._heading_counter += 1
        self.canv.bookmarkPage(key)
        self.canv.addOutlineEntry(title, key, level=level, closed=False)
        self.notify("TOCEntry", (level, title, self.page, key))


SECTIONS = [
    ("1. Comece por aqui", [
        ("p", "O RadioStudio Python Sirius reúne uma fila de reprodução, automação de programação musical, entradas de áudio, gravações e saídas de transmissão. Este manual descreve a versão v1.0.2 Beta, cuja interface está disponível em sete idiomas."),
        ("callout", ("A reprodução local e o envio à transmissão compartilham o áudio do programa. O controle de volume local ajusta a monitoração no computador e não reduz o sinal transmitido. A pré-escuta usa uma saída separada.", False)),
        ("h2", "Uma rotina simples"),
        ("steps", [
            "Adicione músicas à fila Now Playing ou configure pastas indexadas e o AutoDJ.",
            "Escolha a saída e os dispositivos em Configurações.",
            "Pressione Reproduzir ou ligue o AutoDJ para preencher a fila automaticamente.",
            "Se quiser transmitir, configure o servidor embutido ou um perfil externo na aba Transmissão e inicie a transmissão.",
            "Acompanhe o título, o tempo e o visualizador em No ar; consulte o Histórico para conferir eventos.",
        ]),
        ("p", "As seções seguintes explicam cada painel e oferecem tutoriais para as tarefas comuns. Os nomes das opções neste manual correspondem aos rótulos visíveis na interface."),
    ]),
    ("2. Abrir e preparar o programa", [
        ("h2", "Projeto em desenvolvimento"),
        ("p", "No projeto de desenvolvimento, abra Iniciar RadioStudio Python.bat. A primeira execução pode preparar o ambiente e instalar as dependências. Durante o uso, a janela principal contém a fila, o explorador e os painéis de operação."),
        ("h2", "Pacote instalado e versão portátil"),
        ("p", "Na versão instalada, use o atalho criado pelo instalador. Na versão portátil, execute o arquivo Portable. O modo portátil tenta guardar dados na pasta RadioStudioPythonData ao lado do executável; se não puder gravar ali, usa o perfil normal do Windows."),
        ("callout", ("O instalador e o pacote portátil são distribuídos separadamente do projeto Python. Configurações e histórico pertencem ao perfil de dados, não ao arquivo do programa.", False)),
        ("h2", "Primeira configuração"),
        ("steps", [
            "Abra Configurações no topo direito ou Arquivo > Configurações.",
            "Na aba Áudio, selecione Reprodução principal e Escuta / pré-escuta. Deixe Padrão do Windows se não precisar escolher um dispositivo específico.",
            "Na aba Metadados, informe os dados da estação que deseja publicar.",
            "Adicione uma ou mais faixas e confira se o título aparece na fila.",
            "Pressione Reproduzir para testar uma faixa antes de configurar uma transmissão.",
        ]),
    ]),
    ("3. Conheça a janela principal", [
        ("p", "A janela tem uma barra de menus, um painel superior de status e três áreas de trabalho abaixo. As divisórias entre áreas podem ser arrastadas para ajustar o espaço disponível."),
        ("h2", "Menus"),
        ("bullets", [
            "Arquivo: adicionar áudio ou pasta, abrir e salvar playlist, ocultar na bandeja e sair.",
            "Reprodução: reproduzir ou pausar, avançar para a próxima faixa e parar.",
            "Transmissão: iniciar ou parar as saídas.",
            "Configurações: preferências de áudio, dispositivos, metadados, visualizador, AutoDJ e Rich Presence.",
            "Ajuda: Manual do usuário e Sobre.",
        ]),
        ("h2", "Painel superior"),
        ("bullets", [
            "À esquerda, o relógio mostra data, hora e tempo até a próxima hora.",
            "No centro, No ar mostra o estado da transmissão, o título da faixa, a forma de onda, o tempo decorrido e o tempo restante.",
            "Configurações abre as preferências gerais.",
        ]),
        ("h2", "Áreas inferiores"),
        ("bullets", [
            "Lateral esquerda: explorador de arquivos e Histórico de eventos.",
            "Centro: fila Now Playing, controles do player e pré-escuta.",
            "Lateral direita: Agendador, Cart wall, Entradas, Transmissão, AutoDJ e Gravação.",
        ]),
    ]),
    ("4. Adicionar músicas ao Now Playing", [
        ("h2", "Pelo explorador lateral"),
        ("steps", [
            "Use o ícone de pasta para escolher a pasta principal, o computador para ver unidades ou a casa para voltar à pasta de músicas.",
            "Expanda as pastas pelo símbolo +. O duplo clique em uma pasta expande ou recolhe a árvore.",
            "Para inserir uma faixa, dê duplo clique nela ou arraste-a para a fila.",
            "Para inserir vários arquivos, selecione-os no explorador e pressione Adicionar seleção.",
            "Use a lupa para filtrar nomes e o marcador de localização para mostrar o caminho da pasta.",
        ]),
        ("h2", "Pelo menu ou pelos botões da fila"),
        ("steps", [
            "Clique em + Áudio ou use Arquivo > Adicionar áudio para escolher arquivos.",
            "Clique em + Pasta ou use Arquivo > Adicionar pasta para buscar arquivos de áudio dentro de uma pasta.",
            "Os formatos aceitos pelo seletor incluem MP2, MP3, WAV, FLAC, Ogg Vorbis, Opus, AAC, M4A, M4B, WMA, AIFF, AC3, APE e áudio em MKA/MP4.",
        ]),
        ("callout", ("A extensão do arquivo não garante que todos os codecs serão decodificados pelo sistema. Se o Qt Multimedia não conseguir abrir uma faixa, o RadioStudio tenta uma conversão de compatibilidade com o FFmpeg incluído.", True)),
        ("h2", "Metadados da lista"),
        ("p", "A fila mostra Título, Artista e Álbum. O RadioStudio tenta ler as tags do arquivo; quando elas não existem ou não são reconhecidas, o nome do arquivo serve como título. Passe o cursor sobre um item para consultar seu caminho."),
    ]),
    ("5. Organizar, remover e salvar a fila", [
        ("h2", "Mudar a ordem"),
        ("bullets", [
            "Arraste uma linha da fila para a posição desejada. A ordem é atualizada enquanto você arrasta.",
            "Selecione uma faixa e use os botões de seta para cima e para baixo.",
            "Use Ctrl+seta para cima ou Ctrl+seta para baixo para mover a seleção. Com várias linhas selecionadas, o grupo se move junto.",
        ]),
        ("h2", "Remover faixas"),
        ("bullets", [
            "Selecione uma ou várias linhas e pressione Delete ou Remover.",
            "Ctrl+A seleciona todas as faixas da fila; Delete remove a seleção.",
            "Se a faixa em reprodução for removida, a reprodução é parada e o índice atual é limpo.",
        ]),
        ("h2", "Abrir e salvar M3U"),
        ("steps", [
            "Pressione Salvar ou Arquivo > Salvar playlist e escolha um arquivo M3U/M3U8.",
            "Para recuperar a lista, use Abrir ou Arquivo > Abrir playlist.",
            "O arquivo de playlist aponta para os caminhos de áudio. Se mover as músicas, atualize ou recrie a playlist.",
        ]),
        ("p", "O cabeçalho numerado à esquerda da tabela indica a posição da faixa. Não existe uma coluna numérica extra entre a numeração e o título."),
    ]),
    ("6. Reproduzir e pré-escutar", [
        ("h2", "Player principal"),
        ("bullets", [
            "Reproduzir inicia a faixa selecionada ou retoma uma faixa pausada.",
            "Pausar mantém a posição e interrompe temporariamente o áudio.",
            "Parar encerra a reprodução atual. Próxima avança para a próxima posição da fila.",
            "A barra de posição permite buscar outro ponto da faixa; o rótulo mostra tempo atual e duração.",
            "Volume controla a monitoração local. A largura fixa do slider mantém a precisão quando o painel fica estreito.",
        ]),
        ("h2", "Pré-escutar uma faixa"),
        ("steps", [
            "Selecione uma faixa na fila e pressione Pré-escutar seleção, ou use Pré-escutar no menu do botão direito.",
            "A janela Pré-escuta abre com título, artista, álbum, barra de posição e volume próprios.",
            "Use Reproduzir/Pausar e Parar dentro da janela para controlar essa escuta.",
            "A pré-escuta segue o dispositivo Escuta / pré-escuta selecionado em Configurações > Áudio.",
        ]),
        ("callout", ("A pré-escuta é independente do player principal: não troca a faixa no Now Playing e não envia seu áudio à transmissão.", False)),
    ]),
    ("7. Relógio, No ar e visualizadores", [
        ("p", "O painel No ar acompanha a faixa ativa no player principal. O título e o indicador de tempo são atualizados durante a transição para a faixa seguinte. Se uma transmissão estiver ligada, o rótulo NO AR pisca em vermelho."),
        ("h2", "Escolher um visualizador"),
        ("steps", [
            "Abra Configurações > Geral.",
            "Escolha uma opção em Visualizador de ondas.",
            "Feche a janela com OK para salvar; retorne à tela principal para ver o estilo.",
        ]),
        ("p", "Há estilos de forma de onda e visualizadores dinâmicos que reagem ao áudio, incluindo espectros por frequência. Os modos animados atualizam continuamente e podem aumentar o uso de CPU, especialmente em computadores mais fracos. Se notar lentidão, escolha um visualizador estático ou barras clássicas."),
        ("callout", ("A visualização usa o sinal da reprodução atual. Ela serve para acompanhar o áudio, não como medidor calibrado de transmissão.", False)),
    ]),
    ("8. Configurações gerais, áudio e metadados", [
        ("h2", "Geral"),
        ("bullets", [
            "Ao fechar a janela, continuar na bandeja do Windows: mantém o programa aberto em segundo plano. Use Sair no menu Arquivo ou na bandeja para encerrar.",
            "Ao abrir, retomar a música que estava tocando ao sair: restaura a posição salva da faixa na próxima inicialização.",
            "Iniciar transmissão ao abrir se estava ativa ao sair: restaura essa intenção de inicialização.",
            "Controles do player: posiciona os controles abaixo da playlist ou no topo, abaixo de No ar.",
            "Visualizador de ondas: seleciona o estilo exibido no painel superior.",
        ]),
        ("h2", "Áudio"),
        ("bullets", [
            "Reprodução principal define a saída local usada para ouvir o master.",
            "Escuta / pré-escuta define a saída independente da pré-escuta.",
            "Desativar Ouvir o áudio master neste computador silencia a monitoração do master local; a playlist, os carts e as entradas continuam disponíveis para as saídas de transmissão.",
        ]),
        ("h2", "Metadados"),
        ("p", "Em Configurações > Metadados, informe Nome da estação, Descrição, Gênero e Site/URL. O campo Título no ar define o texto enviado com a música."),
        ("bullets", [
            "%title, %artist, %album, %albumartist, %genre, %year, %track, %composer, %filename, %duration e %station são substituídos por dados da faixa ou da estação.",
            "Use %% para inserir um símbolo de porcentagem literal.",
            "A prévia abaixo do campo mostra como o formato fica para uma faixa de exemplo ou para a faixa atual.",
            "Exemplo: %station | %artist - %title (%album).",
        ]),
    ]),
    ("9. Microfone e entradas auxiliares", [
        ("p", "A aba Entradas tem um botão para o Microfone e cinco para Auxiliar 1 a Auxiliar 5. Ative o botão do canal para incluir o áudio no master e nas saídas de transmissão. Cada dispositivo pode falhar de forma independente sem interromper a música."),
        ("h2", "Configurar os dispositivos"),
        ("steps", [
            "Abra Configurações > Entradas.",
            "Escolha o dispositivo do Microfone e, se necessário, de cada auxiliar. Dispositivo padrão do Windows usa a seleção atual do sistema.",
            "No painel Entradas, ligue o botão do canal que deseja usar e confira o estado mostrado abaixo dele.",
            "Se um dispositivo aparecer como ocupado ou incompatível, escolha outra entrada ou feche o aplicativo que está usando o dispositivo.",
        ]),
        ("h2", "Atenuação do microfone"),
        ("p", "O nível da música com microfone ativo define o volume da música enquanto o canal de microfone está ligado. Se Abaixar mais a música enquanto houver fala estiver marcado, o RadioStudio compara o nível do microfone com o Limite para detectar fala e usa Nível da música durante a fala enquanto a voz estiver presente."),
        ("p", "Auxiliares não reduzem a música. O nível de monitoração do computador não altera o ganho de microfone ou auxiliares enviados ao master."),
        ("callout", ("Não abra um microfone real para testar sem intenção de transmitir ou monitorar o ambiente. Confirme o dispositivo e os níveis antes de ligar o canal.", True)),
    ]),
    ("10. Fades e Mix", [
        ("p", "Abra Configurações > Fades e Mix para ajustar as transições da playlist. Fade in e fade out controlam rampas de volume; Mix é o tempo em que duas faixas podem se sobrepor."),
        ("bullets", [
            "Automatizar fade in, fade out e Mix com análise do áudio permite que o analisador avalie introdução, final e nível da faixa em segundo plano.",
            "Fade in por faixa suaviza o início de uma faixa.",
            "Fade out ao parar ou avançar suaviza uma parada ou avanço manual.",
            "Mix / sobreposição define a sobreposição normal ou o limite máximo usado pelo modo automático.",
            "Corte de silêncio permite antecipar a próxima faixa se houver silêncio sustentado perto do final.",
            "Limite de silêncio e Silêncio mínimo detectado ajustam a detecção.",
        ]),
        ("h2", "Tutorial para ajustar transições"),
        ("steps", [
            "Comece com transições automáticas desligadas e ajuste fade in, fade out e Mix manualmente para o estilo da estação.",
            "Ative a opção automática se quiser que a análise escolha uma sobreposição adequada para cada par de faixas.",
            "Ative Corte de silêncio apenas se desejar antecipar a próxima faixa durante uma cauda silenciosa.",
            "Ajuste o limite de silêncio e a duração mínima com cuidado; valores muito sensíveis podem detectar passagens quietas como silêncio.",
            "Teste faixas com finais diferentes e observe a fila e o painel No ar antes de deixar a automação operar sem supervisão.",
        ]),
        ("callout", ("A análise automatizada é uma estimativa baseada no áudio e pode não entender pausas musicais intencionais. O detector de silêncio observa o trecho final da faixa, não toda a música.", True)),
    ]),
    ("11. AutoDJ: visão geral", [
        ("p", "O AutoDJ seleciona músicas do catálogo compartilhado da Biblioteca e completa as posições futuras da fila. Ele não precisa substituir a faixa que já está tocando. O botão no topo da aba alterna entre AutoDJ ligado e desligado."),
        ("bullets", [
            "Sem um bloco de programação ativo, o AutoDJ considera todas as pastas indexadas e ativas da Biblioteca e favorece faixas menos recentes.",
            "Em um bloco ativo, você pode escolher pastas indexadas, coleções, etiquetas ou grupos de rotação como fontes. Se nenhuma pasta nem fonte for marcada, o bloco usa todas as pastas ativas; os filtros refinam a seleção.",
            "Se mais de um bloco se aplicar ao horário, a maior prioridade vence.",
            "Faixas já presentes na fila futura não são adicionadas de novo.",
            "Se os cooldowns deixarem poucas opções, o AutoDJ pode flexibilizar uma regra e mostrar um aviso em vez de deixar a fila vazia.",
        ]),
        ("h2", "Ligar e completar a fila"),
        ("steps", [
            "Adicione e examine ao menos uma pasta na Biblioteca, deixando-a ativa para o AutoDJ.",
            "Ajuste as regras de repetição na parte superior da aba AutoDJ.",
            "Pressione AutoDJ desligado para ligá-lo. O estado é salvo para a próxima abertura.",
            "Use Completar fila para solicitar o preenchimento imediato.",
            "Leia o status no topo da aba. Ele informa exame em andamento, falta de candidatos ou outras condições.",
        ]),
        ("p", "O número de faixas futuras desejado é configurado em Faixas à frente. Os valores de Evitar artista por e Faixa por são janelas em minutos; zero desativa a regra correspondente."),
    ]),
    ("12. Biblioteca compartilhada e organização do acervo", [
        ("h2", "Adicionar uma pasta à Biblioteca"),
        ("steps", [
            "Abra Biblioteca > Pastas indexadas e pressione Adicionar pasta.",
            "Escolha uma pasta no computador e dê um nome fácil de reconhecer.",
            "A pasta aparece na tabela. Mantenha-a ativa para incluí-la no catálogo e permitir que o AutoDJ selecione suas faixas.",
            "Aguarde o exame em segundo plano. O status mostra quantas faixas foram atualizadas e se houve erros.",
            "Se os arquivos mudarem depois, selecione a pasta indexada e pressione Examinar.",
        ]),
        ("p", "A Biblioteca é um catálogo compartilhado, formado pelas pastas indexadas. O índice mantém caminhos e metadados para filtrar as faixas sem reler todos os arquivos na thread da interface. Uma pasta pode continuar indexada, mas inativa; nesse caso, ela sai do catálogo e da seleção do AutoDJ."),
        ("h2", "Pesquisar e organizar faixas"),
        ("bullets", [
            "Na aba Catálogo, pesquise por texto e filtre por pasta indexada, coleção, etiqueta ou grupo de rotação.",
            "Escolha uma coluna no seletor de ordenação para organizar por título, artista, álbum, gênero, ano, duração ou dados de organização.",
            "Use Adicionar à fila, Reproduzir agora, Pré-escutar ou Abrir pasta para trabalhar com as faixas selecionadas.",
            "Crie coleções manuais a partir da seleção ou coleções inteligentes com critérios. As coleções inteligentes acompanham as faixas que correspondem aos critérios.",
            "Etiquetas são livres. Uma faixa pode pertencer a vários grupos de rotação; se grupos selecionados se sobrepuserem, o AutoDJ usa o maior peso e não duplica a faixa.",
        ]),
        ("h2", "Editar metadados"),
        ("steps", [
            "Selecione uma faixa ou várias no Catálogo e pressione Editar metadados.",
            "Marque os campos que deseja alterar. Em lote, somente os campos marcados são aplicados; use Limpar campo quando quiser remover um valor existente.",
            "Edite título, artista, álbum, artista do álbum, gênero, ano, número da faixa, compositor, comentários, letras, capa e tags personalizadas. Duração e propriedades técnicas são somente para consulta.",
            "Escolha Somente no RadioStudio para salvar uma sobreposição interna ou Gravar nas tags do arquivo. A opção padrão fica em Configurações e pode ser substituída em cada salvamento.",
            "Use Desfazer metadados no Catálogo para restaurar a alteração mais recente da faixa.",
        ]),
        ("p", "As sobreposições internas ficam no banco da Biblioteca e são incluídas nos backups. Elas são usadas na Biblioteca, na fila, na reprodução, na transmissão, no Discord e no AutoDJ. Gravar nas tags usa remux sem recodificar o áudio e mantém uma cópia anterior no histórico para desfazer. MP3, M4A, M4B, FLAC, Opus e WAV são os formatos com gravação de tags verificada. Para outros formatos, use a sobreposição interna. Se a faixa estiver tocando, a gravação no arquivo aguarda o fim da reprodução; a sobreposição já fica disponível."),
        ("h2", "Remover ou corrigir"),
        ("p", "Na aba Pastas indexadas, selecione uma ou mais pastas e use Remover. A confirmação informa que os índices serão removidos; o histórico de execução é preservado. Se a pasta for renomeada ou movida, remova a referência antiga e cadastre a nova localização."),
    ]),
    ("13. Programar blocos musicais", [
        ("h2", "Criar um bloco semanal"),
        ("steps", [
            "Abra AutoDJ > Programação e pressione Novo bloco.",
            "Informe o Nome do bloco e deixe Bloco ativo marcado.",
            "Defina o intervalo Das e até, a prioridade e os Dias da semana.",
            "Selecione pastas indexadas e, se quiser, coleções, etiquetas ou grupos na tabela Fontes do bloco e pesos relativos. Essas fontes podem ser combinadas.",
            "Se nenhuma pasta nem fonte for selecionada, o bloco usa todas as pastas ativas da Biblioteca. Se houver uma seleção explícita, o bloco usa essas fontes; os filtros refinam as faixas elegíveis.",
            "Defina os filtros de metadados e salve.",
        ]),
        ("h2", "Regras de horário e prioridade"),
        ("bullets", [
            "Os dias usam o relógio local do computador.",
            "Intervalos que atravessam a meia-noite são aceitos.",
            "O início e o fim do mesmo bloco não podem ser iguais.",
            "Se dois blocos estiverem ativos no mesmo horário, o de maior prioridade é usado.",
            "Blocos sem faixa elegível mostram o motivo no status; o AutoDJ não troca silenciosamente para a seleção fora do bloco.",
        ]),
        ("h2", "Filtros disponíveis"),
        ("bullets", [
            "Gêneros: uma lista separada por vírgulas.",
            "Artistas: nomes exatos separados por vírgulas.",
            "Título/álbum/artista contém: todas as palavras incluídas precisam aparecer nos campos selecionados.",
            "Excluir palavra-chave: uma correspondência elimina a faixa.",
            "Ano: limite mínimo e/ou máximo, deixando vazio quando não quiser limitar.",
            "Buscar palavras em: escolha Título, Artista, Álbum, Artista do álbum, Gênero, Compositor, Ano e Nome/caminho.",
        ]),
        ("p", "Quando terminar, confira a tabela de blocos: ela resume estado, nome, dias, horário, prioridade, fontes e filtros. Edite o bloco para mudar qualquer regra."),
    ]),
    ("14. Repetições e histórico do AutoDJ", [
        ("h2", "Evitar repetições"),
        ("bullets", [
            "Evitar artista por define quanto tempo um artista deve ficar sem tocar.",
            "Evitar faixa por define quanto tempo o mesmo arquivo deve ficar sem tocar.",
            "O cálculo considera execuções anteriores e os itens futuros já enfileirados.",
            "As regras podem ser desativadas individualmente com valor zero.",
            "Remover da fila as faixas anteriores à atual apaga da fila os itens já tocados, preservando a faixa atual e as próximas. O histórico continua gravado.",
        ]),
        ("h2", "Consultar o histórico"),
        ("steps", [
            "Abra AutoDJ > Histórico.",
            "Consulte horário, título, artista, álbum, gênero, ano e caminho do arquivo.",
            "Use Limpar histórico apenas se quiser apagar o registro que alimenta a consulta e as regras de recência.",
        ]),
        ("p", "O histórico registra o início real de uma faixa, incluindo faixas adicionadas manualmente, agendadas e pelo AutoDJ. O banco conserva até 50 mil execuções; os metadados são um retrato no momento do início."),
    ]),
    ("15. Agendar faixas e ações", [
        ("p", "O Agendador executa uma ação em uma data e horário únicos ou em dias da semana escolhidos. A tabela mostra a próxima execução, a ação, o conteúdo e o resultado mais recente."),
        ("steps", [
            "Abra Agendador e pressione Adicionar.",
            "Escolha a Ação: tocar uma faixa, carregar uma playlist M3U, executar um Cart Wall, controlar a reprodução ou iniciar/parar a transmissão.",
            "Para uma faixa ou playlist, selecione o arquivo. Uma playlist usa caminhos relativos à pasta do próprio M3U; arquivos ausentes são ignorados.",
            "Escolha Uma vez e informe a Data, ou escolha Semanal e marque um ou mais Dias da semana. Defina o Horário.",
            "Em Início da reprodução, escolha No horário agendado para interromper a faixa atual com a transição configurada, ou Após a faixa atual para deixar o item na fila.",
            "Para playlists, escolha Inserir mantendo a fila, Substituir a fila ou Carregar sem iniciar. O último modo atualiza a fila sem iniciar áudio.",
            "Deixe Ativo marcado e confirme. Um evento único é desativado depois de executar ou ser perdido; ele permanece visível na tabela.",
            "Para disparar um evento selecionado imediatamente, use Executar agora. Isso não consome a próxima repetição, exceto quando executado no próprio horário agendado.",
        ]),
        ("p", "Eventos de faixa e playlist no mesmo minuto são enfileirados na ordem da tabela. O modo de início do primeiro evento de mídia desse grupo controla se a fila começa imediatamente ou aguarda a faixa atual."),
        ("p", "Use Editar para alterar o evento e Excluir para removê-lo. Se o programa estiver aberto, uma execução pode atrasar até cinco minutos; depois disso, o evento é registrado como perdido. Eventos que venceram enquanto o programa estava fechado não são executados ao abrir."),
        ("callout", ("O agendador usa o horário local do computador e só dispara enquanto o RadioStudio está aberto. Uma transmissão iniciada pelo agendador usa os perfis já configurados.", True)),
    ]),
    ("16. Cart wall", [
        ("p", "O Cart wall oferece 25 botões para efeitos, identificações, vinhetas ou outros áudios curtos. Cada botão é independente da playlist."),
        ("steps", [
            "Clique com o botão direito em um Cart e escolha Atribuir áudio.",
            "Selecione o arquivo. O botão passa a mostrar o título do áudio.",
            "Clique com o botão esquerdo para reproduzir o cart.",
            "Clique com o botão direito e escolha Limpar para remover a atribuição.",
        ]),
        ("p", "O cart é enviado ao master e às saídas de transmissão. Se a monitoração local estiver desativada, a reprodução local fica silenciada, mas o sinal continua disponível para as saídas."),
    ]),
    ("17. Servidor embutido de escuta", [
        ("h2", "Iniciar o stream local"),
        ("steps", [
            "Na aba Transmissão, abra o grupo Servidor embutido.",
            "Marque Ativar servidor de escuta e escolha a Porta.",
            "Ajuste Ouvintes máximos, MP3 (kbps), qualidade do encoder e Nome da rádio se necessário.",
            "Pressione Iniciar transmissão na parte inferior.",
            "Use o endereço exibido e acrescente /live.mp3 em um player compatível para ouvir o stream.",
        ]),
        ("p", "Com Permitir acesso pela rede local desligado, o endereço usa 127.0.0.1 e serve para este computador. Ligado, o endereço mostra o IP desta máquina para outros dispositivos da rede. O firewall do Windows pode pedir autorização ou bloquear conexões."),
        ("p", "O endpoint /status.json apresenta estado local, faixa e ouvintes. Se a porta escolhida estiver ocupada, o RadioStudio tenta portas seguintes e atualiza o endereço mostrado."),
        ("callout", ("O servidor embutido é uma saída local do programa. Para que ouvintes fora da rede local acessem sua rádio, também são necessários roteador, firewall, endereço público e regras de rede adequadas.", True)),
    ]),
    ("18. Configurar servidores externos", [
        ("h2", "Criar um perfil"),
        ("steps", [
            "Na aba Transmissão, em Saídas para servidores externos, pressione Adicionar.",
            "Dê um Nome ao perfil e escolha Icecast 2 (HTTP PUT) ou SHOUTcast (fonte v1/v2 compatível).",
            "Informe endereço, porta e os campos de origem solicitados pelo provedor.",
            "Preencha Bitrate do stream e Qualidade do encoder na aba Áudio e metadados.",
            "Marque Ativo ao iniciar transmissão se esse perfil deve participar das próximas partidas.",
            "Pressione OK e depois Iniciar transmissão. A tabela mostra o estado de cada saída.",
        ]),
        ("h2", "Campos do servidor"),
        ("bullets", [
            "Icecast: normalmente exige host, porta, mount, usuário source e senha de origem. Use TLS/SSL se o serviço fornecer endpoint seguro.",
            "SHOUTcast: use as credenciais e a porta de origem do painel. Stream ID é utilizado para a identificação compatível com DNAS 2.4.7 ou mais recente.",
            "Porta HTTP/admin é opcional e só precisa ser diferente quando o painel administrativo usa outra porta.",
            "Bitrate define a taxa MP3, de 32 a 320 kbps. Qualidade do encoder ajusta custo de CPU versus qualidade.",
        ]),
        ("p", "As senhas de origem e administrativa são guardadas no Gerenciador de Credenciais do Windows. Elas não ficam no arquivo state.json e precisam ser cadastradas novamente ao mudar para outro computador."),
        ("callout", ("A conexão depende das credenciais, do protocolo e da configuração do seu servidor. Confirme o resultado com um servidor real; o manual não garante compatibilidade com todos os provedores.", True)),
    ]),
    ("19. Estado da transmissão e metadados", [
        ("p", "A aba Transmissão lista perfis, tipo, host, estado e ouvintes quando a saída oferece uma API pública de estatísticas. Erros de uma saída são isolados para que uma falha de destino não derrube a reprodução nem as demais saídas."),
        ("h2", "Atualizar informações da estação"),
        ("steps", [
            "Abra Configurações > Metadados e preencha os dados gerais da estação e o formato Título no ar.",
            "Deixe vazios os dados equivalentes de um perfil externo para herdar as configurações gerais.",
            "Se personalizar esses dados no perfil, pare e inicie a transmissão para aplicar as mudanças do perfil.",
        ]),
        ("p", "O título da faixa atual é enviado ao servidor embutido como metadado ICY. Servidores externos podem receber atualizações de título se o perfil incluir acesso administrativo válido."),
        ("h2", "Formato de áudio"),
        ("p", "As saídas de transmissão codificam em MP3 estéreo a 44,1 kHz. O formato original do arquivo reproduzido pode ser diferente; isso não altera o formato do stream."),
    ]),
    ("20. Rich Presence do Discord", [
        ("h2", "Conectar"),
        ("steps", [
            "Abra o cliente Discord para computador e mantenha-o em execução.",
            "No RadioStudio, abra Configurações > Discord e pressione Verificar conexão.",
            "Quando o estado confirmar disponibilidade, ative Rich Presence do RadioStudio.",
            "Escolha tipo de atividade, nome exibido, texto em destaque e se os dados da faixa podem ser compartilhados.",
        ]),
        ("h2", "Personalizar atividade e links"),
        ("bullets", [
            "Linha principal e Linha secundária aceitam %title, %artist, %album, %station e %status.",
            "Texto ao passar sobre a marca personaliza a dica da identidade visual.",
            "Marque Exibir a capa da faixa atual e informe em URL pública da capa um endereço que entregue a imagem da capa atual. A opção Compartilhar dados da faixa atual também precisa estar ativa.",
            "O Discord busca a imagem na Internet. Arquivos no computador e URLs privadas não funcionam; não use endereço 127.0.0.1 nem da rede local.",
            "Para usar /cover do servidor embutido, habilite Disponibilizar capa junto aos metadados do servidor embutido na aba Transmissão e publique esse endpoint em um domínio acessível pela Internet.",
            "Até dois botões podem apontar para links HTTP(S) públicos.",
            "Para um botão Ouvir a rádio, use a URL pública acessível aos ouvintes, não o endereço 127.0.0.1.",
            "Não coloque tokens, senhas ou informações privadas em linhas ou links.",
        ]),
        ("p", "O aplicativo RadioStudio já tem um ID configurado; não é preciso criar um aplicativo no Developer Portal. O Discord não mostra os botões para a própria conta que publicou a atividade. Outra pessoa precisa abrir o perfil, e as opções de privacidade do Discord também podem ocultar a atividade."),
        ("callout", ("A atividade depende do cliente Discord para computador, do canal IPC local e das opções de privacidade do perfil.", True)),
    ]),
    ("21. Fechar, bandeja e dados do usuário", [
        ("h2", "Ocultar ou encerrar"),
        ("bullets", [
            "Se a opção da bandeja estiver ativada, fechar a janela a oculta, mas mantém reprodução, automação e transmissão ativas.",
            "Use Arquivo > Sair ou o comando Sair no ícone da bandeja para fechar o programa de verdade.",
            "Se o app já estiver escondido na bandeja, abrir o iniciador novamente restaura a janela existente.",
        ]),
        ("h2", "Arquivos de dados"),
        ("bullets", [
            "state.json guarda preferências, lista da fila, perfis e regras. As senhas não são guardadas aqui.",
            "events.jsonl contém o histórico recente de interface e eventos.",
            "app.log guarda diagnósticos técnicos com rotação.",
            "music_library.sqlite3 contém o índice das pastas da Biblioteca, a organização do acervo e o histórico do AutoDJ.",
            "Arquivos temporários de decodificação podem ser removidos quando o player libera a faixa.",
        ]),
        ("p", "No modo instalado, os dados ficam em %APPDATA%\\RadioStudioPython, normalmente dentro da pasta AppData\\Roaming do usuário do Windows. No portátil, procure primeiro RadioStudioPythonData ao lado do executável; uma pasta sem permissão de escrita usa o perfil do Windows."),
        ("h2", "Fazer uma cópia de segurança"),
        ("steps", [
            "Encerre o programa pelo comando Sair para permitir que arquivos e banco sejam fechados.",
            "Copie a pasta de dados correspondente para uma área segura.",
            "Para restaurar, feche o RadioStudio e substitua os arquivos da pasta de perfil pela cópia.",
            "Depois de restaurar, abra o programa e confira a fila, as pastas indexadas, o catálogo e o histórico.",
        ]),
        ("callout", ("Uma cópia de segurança não transfere as senhas do Gerenciador de Credenciais do Windows. Ao mudar de computador, informe-as novamente nos perfis de transmissão.", True)),
    ]),
    ("22. Atalhos de teclado", [
        ("bullets", [
            "Ctrl+O: adicionar áudio.",
            "Ctrl+S: salvar playlist.",
            "Ctrl+Q: sair do programa.",
            "Ctrl+Space: reproduzir ou pausar.",
            "Ctrl+Right: próxima faixa.",
            "Ctrl+P: abrir preferências.",
            "Na fila: Ctrl+A seleciona todas as linhas.",
            "Na fila: Delete remove as linhas selecionadas.",
            "Na fila: Ctrl+seta para cima e Ctrl+seta para baixo movem a seleção.",
        ]),
        ("p", "Atalhos da fila são mais previsíveis quando a tabela Now Playing está focada. O menu Reprodução também oferece as ações principais."),
    ]),
    ("23. Solução de problemas", [
        ("h2", "A faixa não toca"),
        ("bullets", [
            "Confirme que o arquivo existe e que seu formato/codec é aceito pelo sistema.",
            "Confira a saída em Configurações > Áudio e teste outro arquivo.",
            "Se o problema continuar, consulte o Histórico na lateral e o arquivo app.log no perfil de dados.",
        ]),
        ("h2", "O AutoDJ não adiciona faixas"),
        ("bullets", [
            "Confirme que o AutoDJ está ligado e que há pelo menos uma pasta indexada e ativa na Biblioteca.",
            "Aguarde o exame terminar e confira a contagem de faixas.",
            "No horário de um bloco, confira dias, intervalo, prioridade, pastas ou fontes selecionadas e filtros.",
            "Uma busca sem resultados não é substituída por seleção aleatória fora do bloco.",
            "Use Completar fila e leia a mensagem de status.",
        ]),
        ("h2", "Não consigo ouvir a transmissão embutida"),
        ("bullets", [
            "Confirme que o Servidor embutido está ativado e que a transmissão foi iniciada.",
            "Use o endereço atual exibido na aba e complete com /live.mp3.",
            "Se o ouvinte estiver em outro computador, ative Permitir acesso pela rede local e confira firewall e rede.",
            "Verifique o limite de Ouvintes máximos e se a porta mudou por estar ocupada.",
        ]),
        ("h2", "O perfil externo desconecta"),
        ("bullets", [
            "Confira host, porta, TLS/SSL, mount/Stream ID e credenciais fornecidos pelo servidor.",
            "A senha pode ter sido alterada no provedor; atualize-a editando o perfil.",
            "Leia o estado na tabela e a mensagem de evento no Histórico.",
            "A aplicação tenta reconectar; falhas persistentes precisam ser verificadas com o painel do servidor e a rede.",
        ]),
        ("h2", "O Discord não mostra a atividade"),
        ("bullets", [
            "Mantenha aberto o cliente Discord para computador, não apenas uma sessão web.",
            "Use Verificar conexão nas configurações Discord.",
            "Confirme se a privacidade de atividade está liberada no Discord.",
            "Peça para outra conta conferir os botões; o Discord não os exibe para quem publicou a atividade.",
            "Para a capa, confirme Compartilhar dados da faixa atual, Exibir a capa da faixa atual e uma URL pública que entregue a imagem atual.",
        ]),
        ("h2", "A interface ficou lenta"),
        ("bullets", [
            "Selecione um visualizador menos animado em Configurações > Geral.",
            "Reduza outras tarefas pesadas de áudio e análise em execução.",
            "Se ocorrer um erro, anote o horário, a ação e a faixa; consulte app.log sem compartilhar senhas ou dados privados.",
        ]),
    ]),
    ("24. Gravação de transmissão e podcasts", [
        ("p", "A aba Gravação captura o mix estéreo final do Master Ao Vivo, depois do ganho master e do limiter. A gravação reúne a música, os carts do Cart wall, o microfone e as entradas auxiliares. O programa pode gravar com a transmissão ativa ou parado; gravar não liga o indicador NO AR."),
        ("h2", "Escolher pasta e formato"),
        ("bullets", [
            "A pasta inicial é Música\\RadioStudio Recordings. Use Escolher pasta para selecionar outro destino; a preferência fica salva.",
            "MP3 é o formato inicial, a 192 kbps. A lista oferece outras taxas de bits para equilibrar tamanho e qualidade.",
            "WAV grava PCM estéreo, 16 bits e 44,1 kHz, útil para edição posterior de podcasts. O formato pode ocupar muito mais espaço que MP3.",
            "Cada arquivo recebe data e hora no nome. Se já existir um arquivo com esse nome, o RadioStudio acrescenta um sufixo para não sobrescrevê-lo.",
        ]),
        ("h2", "Gravar manualmente"),
        ("steps", [
            "Abra a aba Gravação e confira a pasta e o formato escolhidos.",
            "Pressione Iniciar. A gravação começa mesmo quando a transmissão não está no ar.",
            "Use Pausar e Retomar para interromper e continuar a mesma sessão, por exemplo durante uma gravação de podcast.",
            "Pressione Parar gravação para encerrar. Aguarde o estado Gravação finalizada antes de abrir ou mover o arquivo.",
            "A área de estado mostra duração e caminho do arquivo atual. O arquivo só passa a ter o nome final depois que a codificação termina.",
        ]),
        ("h2", "Iniciar junto da transmissão"),
        ("p", "Marque Iniciar gravação automaticamente ao iniciar a transmissão para ligar essa opção. Ela vem desmarcada. Ao parar a transmissão, o RadioStudio encerra apenas a gravação iniciada por esse início automático; uma gravação manual já em curso continua."),
        ("h2", "Criar uma agenda"),
        ("steps", [
            "Na seção Agendas de gravação, pressione Adicionar.",
            "Escolha Única para indicar uma data ou Recorrente para marcar um ou mais dias da semana.",
            "Defina Início e Término e marque Ativa. Horários iguais são inválidos; se Término for anterior a Início, a sessão termina no dia seguinte.",
            "Pressione OK para salvar. Use Editar ou Excluir para atualizar a tabela.",
            "Deixe o RadioStudio aberto no horário. As agendas não iniciam gravações retroativas após abrir o programa.",
        ]),
        ("p", "Só pode haver uma gravação por vez. Se uma agenda ou o início da transmissão disparar enquanto outra gravação estiver ativa, o novo início será ignorado e aparecerá no Histórico. O término automático de uma agenda para somente a sessão que aquela agenda iniciou; ele não encerra uma gravação manual ou iniciada por outra regra."),
        ("callout", ("Se o gravador ou o FFmpeg falhar, a transmissão continua. O RadioStudio informa o erro e preserva o arquivo parcial quando possível; confira a pasta escolhida antes de tentar novamente.", True)),
    ]),
    ("25. Limites desta versão", [
        ("p", "A gravação desta versão é um único mix estéreo do Master Ao Vivo; não há gravação multifaixa nem edição de áudio dentro do RadioStudio. A interface está disponível em português, inglês, espanhol, coreano, japonês, chinês tradicional e chinês simplificado."),
        ("p", "O protocolo de fonte SHOUTcast implementado é o legado v1, com suporte à identificação de Stream ID compatível com DNAS 2.4.7 ou mais recente; o protocolo nativo v2 não está implementado. A disponibilidade simultânea de entradas também depende dos drivers e dispositivos do Windows."),
        ("p", "As listas de formatos indicam extensões aceitas pelo seletor, mas o suporte real depende do codec disponível. A Rich Presence depende do Discord instalado, em execução e acessível pelo canal IPC local."),
        ("p", "Relate bugs usando o contato mostrado em Ajuda > Sobre. Inclua versão, horário, ação realizada e mensagens do Histórico. Não envie senhas, tokens ou cópias do seu banco de dados público."),
        ("callout", (f"Este guia acompanha a versão {VERSION}. Os nomes de opções e recursos podem mudar em versões futuras.", False)),
    ]),
]


def add_section(story, title: str, blocks: list[tuple[str, object]]) -> None:
    story.append(Paragraph(escape(title), STYLES["ManualChapter"]))
    for kind, content in blocks:
        if kind == "p":
            story.append(paragraph(str(content)))
        elif kind == "h2":
            story.append(Paragraph(escape(str(content)), STYLES["ManualSection"]))
        elif kind == "bullets":
            story.extend(bullets(content))
        elif kind == "steps":
            story.extend(steps(content))
        elif kind == "callout":
            story.append(callout(*content))
            story.append(Spacer(1, 6))
    story.append(Spacer(1, 7))


def build_manual() -> None:
    if not LOGO_FILE.is_file():
        raise FileNotFoundError(f"Logo do Sobre não encontrado: {LOGO_FILE}")
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    story = [
        CoverPage(LOGO_FILE),
        NextPageTemplate("Body"),
        PageBreak(),
        Paragraph("Sumário", STYLES["ManualFrontTitle"]),
        paragraph("Selecione um título no sumário para navegar até o capítulo.", "ManualSmall"),
    ]
    toc = TableOfContents()
    toc.levelStyles = [STYLES["ManualTOC1"], STYLES["ManualTOC2"]]
    toc.dotsMinLevel = 0
    story.extend([toc, PageBreak()])
    story.append(Paragraph("Como usar este manual", STYLES["ManualChapter"]))
    story.append(paragraph(
        "O sumário e os marcadores do painel de navegação do leitor de PDF levam aos capítulos. "
        "Tutoriais usam passos numerados; caixas azuis trazem explicações e caixas amarelas "
        "chamam atenção para cuidados ou limitações."
    ))
    story.append(paragraph(
        "A interface pode variar um pouco com o tamanho da janela e a configuração do Windows. "
        "Se uma opção não estiver visível, procure pelo nome indicado nas abas ou menus."
    ))
    story.append(PageBreak())
    for title, blocks in SECTIONS:
        add_section(story, title, blocks)

    doc = ManualDocTemplate(OUTPUT_FILE)
    doc.multiBuild(story)
    print(f"Manual criado: {OUTPUT_FILE}")


if __name__ == "__main__":
    build_manual()
