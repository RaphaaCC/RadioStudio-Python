"""Catalog coverage and explicit localization checks."""

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QMessageBox

from radiostudio.i18n import (
    LANGUAGE_CHOICES,
    SUPPORTED_LANGUAGES,
    install_translations,
    normalize_language,
    read_language_preference,
    resolve_language,
    save_language_preference,
    set_language,
    translate,
    translate_dialog_buttons,
)


UI_TEXT_ARGUMENTS = {
    "QLabel": (0,), "QPushButton": (0,), "QCheckBox": (0,), "QGroupBox": (0,),
    "QLineEdit": (0,), "QAction": (0,), "QMenu": (0,), "QRadioButton": (0,),
    "QProgressDialog": (0, 1), "setText": (0,), "setTitle": (0,),
    "setWindowTitle": (0,), "setPlaceholderText": (0,), "setToolTip": (0,),
    "setStatusTip": (0,), "setWhatsThis": (0,), "setSuffix": (0,),
    "setAccessibleName": (0,),
    "setAccessibleDescription": (0,), "setWindowIconText": (0,),
    "showMessage": (0,), "setLabelText": (0,), "setNameFilter": (0,),
    "setNameFilters": (0,), "setPlainText": (0,), "setHtml": (0,),
    "setTabText": (1,), "setItemText": (1,), "setHorizontalHeaderLabels": (0,),
    "setHeaderLabels": (0,), "addItems": (0,),
    "getOpenFileName": (1, 3), "getOpenFileNames": (1, 3),
    "getSaveFileName": (1, 3), "getExistingDirectory": (1,),
    "getText": (1, 2), "getItem": (1, 2), "getInt": (1, 2), "getDouble": (1, 2),
    "information": (1, 2), "warning": (1, 2), "critical": (1, 2), "question": (1, 2),
    "show_information": (1, 2), "show_warning": (1, 2),
    "show_critical": (1, 2), "ask_question": (1, 2),
}


def call_name(node):
    if isinstance(node.func, ast.Name):
        return node.func.id
    return node.func.attr if isinstance(node.func, ast.Attribute) else ""


def translated_call(node, parents):
    parent = parents.get(node)
    return (isinstance(parent, ast.Call)
            and call_name(parent) in {"translate", "translated_icon_label"})


def is_text_argument(node):
    return (isinstance(node, ast.JoinedStr)
            or isinstance(node, ast.Constant) and isinstance(node.value, str)
            or isinstance(node, ast.Call) and call_name(node) == "translate")


def source_template(node):
    pieces = []
    for part in node.values:
        if isinstance(part, ast.Constant) and isinstance(part.value, str):
            pieces.append(part.value)
        elif isinstance(part, ast.FormattedValue):
            pieces.append("{}")
    return "".join(pieces)


def static_text_parts(node):
    if isinstance(node, (ast.Constant, ast.JoinedStr)):
        yield node
    elif (isinstance(node, ast.Call) and call_name(node) == "translated_icon_label"
          and node.args):
        yield from static_text_parts(node.args[0])
    elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        for item in node.elts:
            yield from static_text_parts(item)
    elif isinstance(node, ast.IfExp):
        yield from static_text_parts(node.body)
        yield from static_text_parts(node.orelse)
    elif isinstance(node, ast.BoolOp):
        for item in node.values:
            yield from static_text_parts(item)
    elif isinstance(node, ast.BinOp):
        yield from static_text_parts(node.left)
        yield from static_text_parts(node.right)


def template_signature(value):
    return re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{}", value)


class TranslationCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = Path(__file__).resolve().parents[1] / "radiostudio" / "translations"
        cls.catalogs = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        }

    def test_every_language_has_a_complete_separate_catalog(self):
        expected_locales = {locale for _, locale in SUPPORTED_LANGUAGES}
        self.assertEqual(set(self.catalogs), expected_locales)
        key_sets = [set(entries) for entries in self.catalogs.values()]
        self.assertTrue(all(keys == key_sets[0] for keys in key_sets[1:]))
        self.assertGreaterEqual(len(key_sets[0]), 640)
        for locale, entries in self.catalogs.items():
            self.assertTrue(all(isinstance(value, str) and value for value in entries.values()), locale)
        self.assertTrue(all(key == value for key, value in self.catalogs["pt_BR"].items()))

    def test_translation_templates_preserve_every_placeholder(self):
        printf_token = re.compile(
            r"%(?:\([^)]+\))?[#0 +-]?(?:\d+|\*)?(?:\.\d+)?[diouxXeEfFgGcrsa%]")
        for locale, entries in self.catalogs.items():
            for source, target in entries.items():
                source_tokens = set(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", source))
                target_tokens = set(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", target))
                self.assertEqual(source_tokens, target_tokens, (locale, source, target))
                self.assertEqual(sorted(printf_token.findall(source)),
                                 sorted(printf_token.findall(target)),
                                 (locale, source, target))

    def test_every_catalog_entry_is_retrievable_by_explicit_lookup(self):
        for locale, entries in self.catalogs.items():
            for source, expected in entries.items():
                self.assertEqual(translate(source, locale), expected, (locale, source))

    def test_network_port_uses_the_correct_technical_term(self):
        expected = {
            "en_US": "Port",
            "es_MX": "Puerto",
            "ko_KR": "포트",
            "ja_JP": "ポート",
            "zh_TW": "連接埠",
            "zh_CN": "端口",
        }
        for locale, value in expected.items():
            self.assertEqual(self.catalogs[locale]["Porta"], value)

    def test_dynamic_audio_labels_and_network_address_are_translated(self):
        expected_host = {
            "en_US": "This computer's IP",
            "es_MX": "IP de este equipo",
            "ko_KR": "이 컴퓨터의 IP",
            "ja_JP": "このコンピューターのIP",
            "zh_TW": "這台電腦的 IP",
            "zh_CN": "这台电脑的 IP",
        }
        for locale, host in expected_host.items():
            self.assertEqual(translate("IP desta máquina", locale), host)
            channel = translate("Microfone", locale)
            tooltip = translate(
                "Ativar ou desativar {channel} no master", locale
            ).format(channel=channel)
            self.assertNotIn("Microfone", tooltip)
            self.assertIn(channel, tooltip)
            message = translate("{v0} enviado ao master", locale).format(v0=channel)
            self.assertIn(channel, message)
            self.assertNotIn("enviado ao master", message)

    def test_library_maintenance_busy_messages_are_translated(self):
        sources = (
            "Aguarde as operações do AutoDJ e da biblioteca terminarem antes de importar configurações.",
            "Aguarde as operações do AutoDJ e da biblioteca terminarem antes de verificar o banco de dados.",
        )
        for locale, catalog in self.catalogs.items():
            for source in sources:
                self.assertIn(source, catalog, (locale, source))
                if locale != "pt_BR":
                    self.assertNotEqual(catalog[source], source, (locale, source))

    def test_icon_labels_keep_translations_without_legacy_emoji(self):
        from radiostudio.ui.icons import translated_icon_label

        try:
            for locale in self.catalogs:
                set_language(locale)
                label = translated_icon_label("📅 Agendador", "📅")
                expected = translate("📅 Agendador", locale).removeprefix("📅").strip()
                self.assertEqual(label, expected, locale)
                self.assertNotIn("📅", label, locale)
        finally:
            set_language("pt_BR")

    def test_control_api_settings_are_localized_in_all_catalogs(self):
        sources = (
            "API", "API REST", "Ativar API REST",
            "Controle a reprodução e a fila por HTTP a partir de aplicativos, scripts ou painéis web na rede local.",
            "Autenticação", "Token automático", "Login e senha", "Nome de usuário", "Senha",
            "Deixe vazio para manter a senha salva.", "Gerar token", "Copiar token",
            "Token de acesso", "Origens CORS permitidas",
            "Para painéis web, informe as origens exatas separadas por vírgula. Deixe vazio para clientes nativos e scripts.",
            "⚠️ HTTP não criptografa a conexão. Login, senha, token e comandos podem ser observados na rede; use apenas em uma rede confiável.",
            "O RadioStudio não altera o Firewall do Windows. Se outros dispositivos não conseguirem conectar, confira as regras de entrada da rede.",
            "Endereços para conexão", "Origem CORS inválida. Use somente protocolo, host e porta.",
            "Informe um nome de usuário para a autenticação.",
            "Informe uma senha para esse nome de usuário.",
            "O nome de usuário não pode conter dois-pontos.",
            "Não foi possível salvar as credenciais da API REST no Gerenciador de Credenciais do Windows.",
            "A API REST não iniciou. Verifique a porta e as credenciais.",
            "A API REST não iniciou: {error}", "http://192.168.1.20:3000",
        )
        unchanged_acronyms = {"API", "API REST", "Copiar token", "http://192.168.1.20:3000"}
        for locale, catalog in self.catalogs.items():
            for source in sources:
                self.assertIn(source, catalog, (locale, source))
                self.assertEqual(translate(source, locale), catalog[source], (locale, source))
                if locale != "pt_BR" and source not in unchanged_acronyms:
                    self.assertNotEqual(catalog[source], source, (locale, source))

    def test_dynamic_messages_translate_without_losing_values(self):
        translated = translate("Erro na pré-escuta: decoder unavailable", "en_US")
        self.assertEqual(translated, "Preview error: decoder unavailable")
        for locale in self.catalogs:
            message = translate(
                "Saídas externas também não puderam iniciar: {error}", locale
            ).format(error="network unavailable")
            self.assertIn("network unavailable", message)
            if locale != "pt_BR":
                self.assertNotIn("Saídas externas", message)

    def test_autodj_feedback_is_catalogued_in_every_language(self):
        sources = (
            "Falha ao carregar o catálogo da Biblioteca para o AutoDJ.",
            "A Biblioteca não tem faixas indexadas nas pastas habilitadas para o AutoDJ.",
            "Ative ao menos uma pasta indexada da Biblioteca para usar o AutoDJ.",
            "As pastas selecionadas para este bloco estão desabilitadas ou indisponíveis; ajuste a seleção.",
            "Sem faixas inéditas na fila; aguardando a reprodução avançar.",
            "Nenhuma faixa disponível para as fontes e os filtros selecionados.",
            "A janela de repetição de artista foi flexibilizada: a biblioteca tem poucas opções.",
            "A janela de repetição da faixa foi flexibilizada: a biblioteca tem poucas opções.",
            "As janelas de repetição foram flexibilizadas: a biblioteca tem poucas opções.",
            "Todas as faixas estão dentro da janela de repetição.",
            "Os arquivos candidatos desapareceram; examine novamente as bibliotecas.",
            "biblioteca pequena; evitei repetir faixas na fila",
            "Sem um bloco de programação ativo, o AutoDJ usa o catálogo inteiro da Biblioteca: todas as pastas indexadas e habilitadas para o AutoDJ. Em cada bloco, você pode limitar as faixas a pastas indexadas, coleções, grupos ou etiquetas; os filtros refinam essa seleção.",
            "Configure pastas indexadas e blocos de programação",
            "Todas as pastas habilitadas para o AutoDJ",
            "Pasta indisponível ({id})",
            "{program_name}: fila completa",
            "{program_name}: {count} faixa(s) adicionada(s)",
            "{program_name}: fila atualizada",
        )
        for locale, catalog in self.catalogs.items():
            for source in sources:
                self.assertIn(source, catalog, (locale, source))
                self.assertEqual(translate(source, locale), catalog[source])
                if locale != "pt_BR":
                    self.assertNotEqual(catalog[source], source, (locale, source))
        self.assertEqual(translate("Aleatório: fila atualizada", "en_US"),
                         "Aleatório: queue updated")
        self.assertEqual(translate("Aleatório: 4 faixa(s) adicionada(s)", "en_US"),
                         "Aleatório: 4 track(s) added")
        self.assertEqual(
            translate("{program_name}: {count} faixa(s) adicionada(s)", "en_US").format(
                program_name="Evening", count=2),
            "Evening: 2 track(s) added")

    def test_dynamic_clock_hint_uses_the_selected_catalog(self):
        from radiostudio.ui.studio_widgets import StudioClock

        app = QApplication.instance() or QApplication([])
        try:
            for locale in ("pt_BR", "en_US", "es_MX", "ko_KR", "ja_JP", "zh_TW", "zh_CN"):
                install_translations(app, locale)
                clock = StudioClock()
                template = translate("Próxima hora em {hours}:{minutes}", locale)
                prefix = template.split("{hours}", 1)[0]
                self.assertTrue(clock.hour_label.text().startswith(prefix),
                                (locale, clock.hour_label.text()))
                clock.deleteLater()
            app.processEvents()
        finally:
            install_translations(app, "pt_BR")

    def test_main_menu_action_labels_use_the_selected_catalog(self):
        from types import SimpleNamespace

        from PySide6.QtWidgets import QMainWindow

        from radiostudio.config import APP_NAME
        from radiostudio.ui.main_window import RadioStudio

        app = QApplication.instance() or QApplication([])
        menu_sources = (
            ("&Arquivo", ("Adicionar áudio...", "Adicionar pasta...", "Abrir playlist...",
                           "Salvar playlist...", "Ocultar na bandeja", "Sair")),
            ("&Reprodução", ("Reproduzir / Pausar", "Próxima faixa", "Parar")),
            ("&Transmissão", ("Iniciar transmissão", "Parar transmissão")),
            ("&Configurações", ("Preferências...",)),
            ("A&juda", ("Manual do usuário", f"Sobre o {APP_NAME}")),
        )
        try:
            for locale in ("pt_BR", "en_US", "es_MX", "ko_KR", "ja_JP", "zh_TW", "zh_CN"):
                install_translations(app, locale)
                window = QMainWindow()
                noop = lambda *_args: None
                for name in ("add_files", "add_folder", "open_m3u", "save_m3u", "hide_to_tray",
                             "request_exit", "toggle_playback", "next_track", "stop",
                             "show_settings", "open_user_manual", "show_about"):
                    setattr(window, name, noop)
                window.broadcast_panel = SimpleNamespace(start=noop, stop=noop)
                RadioStudio.build_menu_bar(window)
                menus = [action.menu() for action in window.menuBar().actions()]
                self.assertEqual([menu.title() for menu in menus],
                                 [translate(title, locale) for title, _ in menu_sources])
                for menu, (_title, expected_sources) in zip(menus, menu_sources):
                    actual = [action.text() for action in menu.actions() if not action.isSeparator()]
                    self.assertEqual(actual, [translate(label, locale) for label in expected_sources],
                                     (locale, menu.title(), actual))
                window.deleteLater()
            app.processEvents()
        finally:
            install_translations(app, "pt_BR")

    def test_backup_default_statuses_have_translations(self):
        statuses = (
            "Backups periódicos ativados. A frequência começa a contar após salvar as configurações.",
            "Backups automáticos ficam desativados até você ativá-los.",
        )
        for locale in self.catalogs:
            for source in statuses:
                translated = translate(source, locale)
                self.assertTrue(translated)
                if locale != "pt_BR":
                    self.assertNotEqual(translated, source, (locale, source))

    def test_dynamic_toolbar_and_cover_titles_use_catalog_entries(self):
        sources = (
            "Escolher pasta de músicas", "Mostrar unidades deste computador", "Pasta acima",
            "Pasta de músicas", "Filtrar arquivos de áudio pelo nome", "Visualizar capa",
            "Capa da faixa no ar", "Capa da faixa selecionada",
        )
        for locale in self.catalogs:
            for source in sources:
                translated = translate(source, locale)
                self.assertTrue(translated)
                if locale != "pt_BR":
                    self.assertNotEqual(translated, source, (locale, source))

        path = Path(__file__).resolve().parents[1] / "radiostudio" / "ui" / "main_window.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {child: parent for parent in ast.walk(tree)
                   for child in ast.iter_child_nodes(parent)}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or call_name(node) not in {"setToolTip", "setWindowTitle"}:
                continue
            if not node.args or not isinstance(node.args[0], ast.Name):
                continue
            if node.args[0].id not in {"tooltip", "title"}:
                continue
            self.assertEqual(call_name(parents[node.args[0]]), "translate",
                             (node.lineno, ast.unparse(node)))

    def test_settings_dialog_status_and_unit_suffixes_use_catalog(self):
        from radiostudio.services.track_metadata import DEFAULT_STATION_METADATA
        from radiostudio.ui.settings_dialog import SettingsDialog

        app = QApplication.instance() or QApplication([])
        install_translations(app, "en_US")
        statuses = (
            (True, "Backups periódicos ativados. A frequência começa a contar após salvar as configurações.",
             "Periodic backups are enabled. The interval starts after you save the settings."),
            (False, "Backups automáticos ficam desativados até você ativá-los.",
             "Automatic backups stay off until you enable them."),
        )
        try:
            for enabled, source, expected in statuses:
                dialog = SettingsDialog(
                    "", "", True, DEFAULT_STATION_METADATA,
                    backup_periodic_enabled=enabled,
                    metadata_update_mode="interval")
                try:
                    self.assertEqual(dialog.windowTitle(), "Settings")
                    self.assertEqual(dialog.backup_status.text(), expected)
                    self.assertEqual(dialog.backup_retention.suffix(), translate(" backups", "en_US"))
                    self.assertEqual(dialog.metadata_update_interval.suffix(),
                                     translate(" segundos", "en_US"))
                finally:
                    dialog.close()
                    dialog.deleteLater()
                    app.processEvents()
        finally:
            install_translations(app, "pt_BR")

    def test_database_and_cart_terminology_stays_on_topic(self):
        wrong_database_terms = {
            "en_US": re.compile(r"\b(?:bank|bench|seat)\b", re.IGNORECASE),
            "es_MX": re.compile(r"\b(?:banco|bancaria|asiento)\b", re.IGNORECASE),
            "ko_KR": re.compile(r"은행"),
            "ja_JP": re.compile(r"銀行|ベンチ"),
            "zh_TW": re.compile(r"銀行|座椅"),
            "zh_CN": re.compile(r"银行|座椅"),
        }
        database_sources = [source for source in self.catalogs["pt_BR"]
                            if "banco" in source.lower()]
        for locale, pattern in wrong_database_terms.items():
            for source in database_sources:
                self.assertIsNone(pattern.search(self.catalogs[locale][source]),
                                  (locale, source, self.catalogs[locale][source]))

        cart_sources = (
            "Cart {v1}", "Cart {number}", "Áudio do cart",
            "Não foi possível decodificar o cart: {error}",
            "Não foi possível reproduzir o cart: {filename}",
            "Preparando cart para compatibilidade: {filename}",
        )
        wrong_cart_terms = {
            "es_MX": re.compile(r"carrit|carro", re.IGNORECASE),
            "ko_KR": re.compile(r"장바구니"),
            "ja_JP": re.compile(r"カート"),
            "zh_TW": re.compile(r"購物車|車音響"),
            "zh_CN": re.compile(r"购物车|车音响"),
        }
        for locale, pattern in wrong_cart_terms.items():
            for source in cart_sources:
                self.assertIsNone(pattern.search(self.catalogs[locale][source]),
                                  (locale, source, self.catalogs[locale][source]))

    def test_supported_locale_selection_and_system_fallback(self):
        locales = {locale for _, locale in SUPPORTED_LANGUAGES}
        for locale in locales:
            self.assertEqual(resolve_language(locale), locale)
        self.assertIn(resolve_language("system"), locales)
        self.assertIsNone(normalize_language("fr_FR"))
        self.assertEqual(normalize_language("zh-CN"), "zh_CN")
        self.assertIn(("Automático (idioma do sistema)", "system"), LANGUAGE_CHOICES)

    def test_language_preference_persistence_respects_state_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state.json"
            preference = root / "language.cfg"
            installer = root / "install-language.cfg"
            installer.write_text("es_MX\n", encoding="utf-8")
            self.assertEqual(read_language_preference(state, preference, installer), ("es_MX", True))
            save_language_preference(preference, "ja_JP")
            self.assertEqual(read_language_preference(state, preference, installer), ("ja_JP", True))
            state.write_text('{"language_preference":"ko_KR"}', encoding="utf-8")
            self.assertEqual(read_language_preference(state, preference, installer), ("ko_KR", True))

    def test_every_static_ui_literal_is_explicitly_wrapped_in_catalog_lookup(self):
        root = Path(__file__).resolve().parents[1] / "radiostudio"
        source_keys = set(self.catalogs["en_US"])
        signatures = {
            locale: {template_signature(key) for key in entries}
            for locale, entries in self.catalogs.items()
        }
        missing_wrappers = []
        missing_keys = []
        paths = [*root.joinpath("ui").rglob("*.py"), root / "app.py"]
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            parents = {child: parent for parent in ast.walk(tree)
                       for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = call_name(node)
                if name == "translate" and node.args:
                    source = node.args[0]
                    for candidate in static_text_parts(source):
                        if isinstance(candidate, ast.Constant):
                            if (isinstance(candidate.value, str) and candidate.value.strip()
                                    and re.search(r"[A-Za-zÀ-ÿ]", candidate.value)
                                    and candidate.value not in source_keys):
                                missing_keys.append((str(path.relative_to(root)),
                                                     candidate.lineno, candidate.value))
                        else:
                            signature = source_template(candidate)
                            if re.search(r"[A-Za-zÀ-ÿ]", signature):
                                for locale, keys in signatures.items():
                                    if signature not in keys:
                                        missing_keys.append((str(path.relative_to(root)),
                                                             candidate.lineno,
                                                             f"{locale}: {signature}"))
                if name in {"addTab", "insertTab"}:
                    indexes = (len(node.args) - 1,) if node.args else ()
                elif name in {"addRow", "insertRow"}:
                    indexes = (0,)
                elif name in {"addItem", "insertItem", "addAction", "addMenu"}:
                    indexes = (1,) if node.args and not is_text_argument(node.args[0]) else (0,)
                else:
                    indexes = UI_TEXT_ARGUMENTS.get(name, ())
                for index in indexes:
                    if not 0 <= index < len(node.args):
                        continue
                    arg = node.args[index]
                    for candidate in static_text_parts(arg):
                        if translated_call(candidate, parents):
                            if isinstance(candidate, ast.Constant):
                                if (isinstance(candidate.value, str)
                                        and re.search(r"[A-Za-zÀ-ÿ]", candidate.value)
                                        and candidate.value not in source_keys):
                                    missing_keys.append((str(path.relative_to(root)),
                                                         candidate.lineno, candidate.value))
                            else:
                                signature = source_template(candidate)
                                if (re.search(r"[A-Za-zÀ-ÿ]", signature)
                                        and signature not in signatures["en_US"]):
                                    missing_keys.append((str(path.relative_to(root)),
                                                         candidate.lineno, signature))
                            continue
                        if isinstance(candidate, ast.Constant) and not re.search(
                                r"[A-Za-zÀ-ÿ]", str(candidate.value)):
                            continue
                        missing_wrappers.append((str(path.relative_to(root)), candidate.lineno))
        self.assertEqual(missing_wrappers, [])
        self.assertEqual(missing_keys, [])

    def test_labels_supplied_by_option_lists_exist_in_every_catalog(self):
        from radiostudio.services.audio_inputs import INPUT_LABELS
        from radiostudio.services.autodj import DAY_NAMES
        from radiostudio.services.broadcast import ENCODER_QUALITY_PRESETS
        from radiostudio.services.process_priority import PROCESS_PRIORITY_OPTIONS
        from radiostudio.ui.studio_widgets import WAVEFORM_STYLES

        labels = set(INPUT_LABELS.values()) | set(DAY_NAMES)
        for options in (ENCODER_QUALITY_PRESETS, PROCESS_PRIORITY_OPTIONS, WAVEFORM_STYLES,
                        LANGUAGE_CHOICES):
            labels.update(label for label, _value in options)
        labels.update({
            "+ Áudio", "+ Pasta", "Adicionar", "Abrir", "Salvar", "↑", "↓", "Remover",
            "Editar", "Excluir", "Executar agora", "▶ Reproduzir", "⏸ Pausar",
            "⏹ Parar", "⏭ Próxima", "➕ Adicionar pasta", "🔄 Examinar",
            "🗑 Remover", "➕ Novo bloco", "✏️ Editar", "🗑 Excluir",
            "Ouvindo", "Jogando", "Assistindo", "Competindo", "Nome do app",
            "Estado", "Detalhes", "A cada 1 hora", "A cada 6 horas", "A cada 12 horas",
            "A cada 24 horas", "A cada 7 dias", "Título", "Artista", "Álbum",
            "Artista do álbum", "Gênero", "Compositor", "Ano", "Nome/caminho",
            "🟢 AutoDJ ligado", "🎙 Microfone", "Deixe vazio para manter a senha salva",
            "Icecast 2: envio HTTP PUT para um mount. Informe o usuário de origem e o mount.",
            "SHOUTcast: conexão de fonte compatível. O Stream ID identifica a transmissão em DNAS 2.4.7 ou mais recente.",
        })
        for locale, catalog in self.catalogs.items():
            self.assertTrue(labels.issubset(catalog),
                            (locale, sorted(labels - set(catalog))))

    def test_explicit_catalog_calls_localize_ui_and_leave_user_data_alone(self):
        app = QApplication.instance() or QApplication([])
        translations = {
            "en_US": "General", "es_MX": "General", "ko_KR": "일반",
            "ja_JP": "一般", "zh_TW": "一般", "zh_CN": "常规",
        }
        try:
            for locale, expected in translations.items():
                install_translations(app, locale)
                ui_label = QLabel(translate("Geral"))
                user_data_label = QLabel("Geral")
                self.assertEqual(ui_label.text(), expected)
                self.assertEqual(user_data_label.text(), "Geral")
                buttons = QDialogButtonBox(
                    QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
                translate_dialog_buttons(buttons)
                self.assertEqual({button.text() for button in buttons.buttons()}, {
                    translate("OK", locale), translate("Cancelar", locale),
                })
        finally:
            install_translations(app, "pt_BR")

    def test_message_box_standard_buttons_use_the_selected_catalog(self):
        from radiostudio.ui.message_boxes import create_message_box

        app = QApplication.instance() or QApplication([])
        buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        try:
            for locale in ("en_US", "es_MX", "ko_KR", "ja_JP", "zh_TW", "zh_CN"):
                install_translations(app, locale)
                box = create_message_box(
                    None, QMessageBox.Icon.Question, translate("Confirmar"),
                    translate("Deseja continuar?"), buttons, QMessageBox.StandardButton.Cancel)
                self.assertEqual(
                    {button.text() for button in box.buttons()},
                    {translate("Sim"), translate("Cancelar")},
                )
                self.assertEqual(box.defaultButton().text(), translate("Cancelar"))
                box.deleteLater()
        finally:
            install_translations(app, "pt_BR")

    def test_message_box_can_use_the_new_language_before_process_switch(self):
        from radiostudio.ui.message_boxes import create_message_box

        app = QApplication.instance() or QApplication([])
        install_translations(app, "pt_BR")
        box = create_message_box(
            None, QMessageBox.Icon.Question,
            translate("Alteração de idioma", "en_US"),
            translate(
                "Deseja reiniciar o RadioStudio agora para aplicar as alterações de idioma?",
                "en_US"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
            locale="en_US",
        )
        self.assertEqual(box.windowTitle(), "Language change")
        self.assertEqual(
            {button.text() for button in box.buttons()}, {"Yes", "No"})
        box.deleteLater()
        app.processEvents()

    def test_dynamic_on_air_title_preserves_track_name_and_localizes_empty_state(self):
        from radiostudio.ui.studio_widgets import OnAirPanel

        app = QApplication.instance() or QApplication([])
        try:
            for locale in ("en_US", "es_MX", "ko_KR", "ja_JP", "zh_TW", "zh_CN"):
                install_translations(app, locale)
                panel = OnAirPanel()
                panel.clear_track()
                self.assertEqual(panel.title.text(),
                                 translate("Nenhuma faixa em reprodução", locale))
                panel.show_track("Geral", "track.mp3")
                self.assertEqual(panel.title.text(), "Geral")
                panel.shutdown_analysis()
                panel.deleteLater()
                app.processEvents()
        finally:
            install_translations(app, "pt_BR")


if __name__ == "__main__":
    unittest.main()
