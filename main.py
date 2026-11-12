# main.py
import argparse
import os
import re
import sys
from pathlib import Path
from copy import deepcopy

from PyQt6.QtCore import QDate, QThread, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from db import AuthorDB
from app_paths import AppPaths, AmbiguousDatabaseError, absolute_path, resolve_paths
from generator import (
    generate_module_documents,
    import_modules_from_excel,
    scan_listings_directory,
)
from ui_shell import (
    APP_NAME, MODERN_QSS, SECTIONS, ElidedLabel, HeroBanner,
    PlannedSection, Sidebar, section_icon,
)





def set_hint_and_tooltip(widget, hint_text: str, is_default_value: bool = False):
    """Устанавливает одинаковую подсказку для Placeholder и ToolTip при наведении."""
    if hasattr(widget, "setPlaceholderText"):
        widget.setPlaceholderText(hint_text)
    if is_default_value and hasattr(widget, "setText"):
        widget.setText(hint_text)
    widget.setToolTip(hint_text)


def format_russian_phone(raw_text: str) -> str:
    """Форматирует вводимый номер телефона в вид: 8 (999) 999-99-99."""
    digits = "".join(ch for ch in raw_text if ch.isdigit())
    if not digits:
        return ""
    if digits[0] in ("7", "8"):
        sub = digits[1:11]
    else:
        sub = digits[:10]

    if not sub:
        return "8"
    if len(sub) <= 3:
        return f"8 ({sub}"
    if len(sub) <= 6:
        return f"8 ({sub[:3]}) {sub[3:]}"
    if len(sub) <= 8:
        return f"8 ({sub[:3]}) {sub[3:6]}-{sub[6:]}"
    return f"8 ({sub[:3]}) {sub[3:6]}-{sub[6:8]}-{sub[8:10]}"


def format_date_ddmmyyyy(raw_text: str) -> str:
    """Форматирует вводимую дату выдачи паспорта в вид: 11.01.2006."""
    digits = "".join(ch for ch in raw_text if ch.isdigit())[:8]
    if len(digits) <= 2:
        return digits
    if len(digits) <= 4:
        return f"{digits[:2]}.{digits[2:]}"
    return f"{digits[:2]}.{digits[2:4]}.{digits[4:8]}"


def format_department_code(raw_text: str) -> str:
    """Форматирует код подразделения в вид: 999-999."""
    digits = "".join(ch for ch in raw_text if ch.isdigit())[:6]
    if len(digits) <= 3:
        return digits
    return f"{digits[:3]}-{digits[3:6]}"


def open_folder_in_explorer(folder_path: str):
    """Автоматически открывает папку с результатами в Проводнике Windows / файловом менеджере."""
    try:
        if sys.platform == "win32":
            os.startfile(folder_path)
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))
    except Exception as e:
        print(f"Не удалось автоматически открыть папку: {e}")


class GenerationWorker(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(int, str)
    failed = pyqtSignal(str)
    module_completed = pyqtSignal(int, int)  # Строка таблицы и пересчитанные страницы.

    def __init__(
        self,
        tasks: list[dict],
        templates_dir: Path,
        output_dir: Path,
        sign_date: str,
        convert_pdf: bool,
    ):
        super().__init__()
        self.tasks = deepcopy(tasks)
        self.templates_dir = templates_dir
        self.output_dir = output_dir
        self.sign_date = sign_date
        self.convert_pdf = convert_pdf

    def run(self):
        try:
            total = len(self.tasks)
            for idx, task in enumerate(self.tasks, start=1):
                prog_name = task["module"]["program_name"]
                self.progress.emit(
                    int((idx - 1) / total * 100),
                    f"Обработка ({idx}/{total}): «{prog_name}»...",
                )
                try:
                    generate_module_documents(
                        index=idx,
                        module_info=task["module"],
                        selected_authors=task["authors"],
                        main_author=task["main_author"],
                        templates_dir=self.templates_dir,
                        output_base_dir=self.output_dir,
                        sign_date=self.sign_date,
                        convert_pdf=self.convert_pdf,
                    )
                finally:
                    # Страницы сохраняются и при последующей ошибке экспорта PDF.
                    self.module_completed.emit(idx - 1, int(task["module"]["listing_pages"]))
            self.progress.emit(100, "Генерация всех пакетов документов успешно завершена!")
            self.finished_ok.emit(total, str(self.output_dir.resolve()))
        except Exception as e:
            self.failed.emit(str(e))


class DocumentsWidget(QWidget):
    generation_stopped = pyqtSignal()

    def __init__(self, paths: AppPaths, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.worker: GenerationWorker | None = None
        self.suppress_notifications = False
        self._generation_outcome = None
        self._generation_controls = []

        self.db = AuthorDB(paths.db_path, data_dir=paths.data_dir)
        self.authors_cache: list[dict] = []
        self.modules_data: list[dict] = []
        self.selected_author_id_for_edit: int | None = None
        self.current_sig_png_path: str = ""
        self.current_sig_pfx_path: str = ""
        self.updating_ui = False

        self._build_ui()
        self._refresh_authors_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        database_bar = QFrame()
        database_bar.setObjectName("DatabaseBar")
        database_layout = QHBoxLayout(database_bar)
        database_layout.setContentsMargins(14, 9, 12, 9)
        database_layout.setSpacing(10)
        database_layout.addWidget(QLabel("База авторов:"))
        database_path = QLineEdit(str(self.paths.db_path))
        database_path.setReadOnly(True)
        database_path.setToolTip(str(self.paths.db_path))
        database_layout.addWidget(database_path, 1)
        open_database_folder = QPushButton()
        open_database_folder.setObjectName("SecondaryBtn")
        open_database_folder.setIcon(section_icon("folder"))
        open_database_folder.setFixedWidth(42)
        open_database_folder.setToolTip("Открыть папку базы авторов")
        open_database_folder.clicked.connect(lambda: open_folder_in_explorer(str(self.paths.db_path.parent)))
        database_layout.addWidget(open_database_folder)
        status = QLabel("●  База подключена")
        status.setObjectName("DatabaseStatus")
        database_layout.addWidget(status)
        root_layout.addWidget(database_bar)

        self.tabs = QTabWidget()
        self.tab_authors = QWidget()
        self.tab_modules = QWidget()
        modules_scroll = QScrollArea()
        modules_scroll.setWidgetResizable(True)
        modules_scroll.setFrameShape(QFrame.Shape.NoFrame)
        modules_scroll.setWidget(self.tab_modules)
        self.tabs.addTab(self.tab_authors, "👤 1. База авторов")
        self.tabs.addTab(modules_scroll, "📦 2. Модули, авторы и генерация документов")
        root_layout.addWidget(self.tabs, stretch=1)

        self._build_authors_tab()
        self._build_modules_tab()

    # --------------------------------------------------------------------------
    # Вкладка 1: База авторов
    # --------------------------------------------------------------------------
    def _build_authors_tab(self):
        layout = QHBoxLayout(self.tab_authors)
        layout.setContentsMargins(12, 12, 12, 12)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_group = QGroupBox("Список сохраненных авторов в локальной БД")
        left_vbox = QVBoxLayout(left_group)

        self.tbl_authors = QTableWidget(0, 6)
        self.tbl_authors.setHorizontalHeaderLabels([
            "ID", "ФИО автора", "Дата рожд.", "Телефон", "Подпись", "Творческий вклад"
        ])
        self.tbl_authors.verticalHeader().setVisible(False)
        self.tbl_authors.setAlternatingRowColors(True)
        self.tbl_authors.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_authors.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl_authors.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl_authors.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.tbl_authors.setColumnWidth(0, 42)
        self.tbl_authors.setColumnWidth(2, 90)
        self.tbl_authors.setColumnWidth(3, 135)
        self.tbl_authors.setColumnWidth(4, 90)
        self.tbl_authors.itemSelectionChanged.connect(self._on_author_table_selected)
        left_vbox.addWidget(self.tbl_authors)

        btn_row_left = QHBoxLayout()
        btn_new_author = QPushButton("➕ Новый автор (очистить форму)")
        btn_new_author.setObjectName("SecondaryBtn")
        btn_new_author.clicked.connect(self._clear_author_form)

        btn_del_author = QPushButton("🗑 Удалить выбранного автора")
        btn_del_author.setObjectName("DangerBtn")
        btn_del_author.clicked.connect(self._delete_selected_author)

        btn_row_left.addWidget(btn_new_author)
        btn_row_left.addStretch()
        btn_row_left.addWidget(btn_del_author)
        left_vbox.addLayout(btn_row_left)

        right_group = QGroupBox("Карточка автора (добавление / редактирование)")
        right_group.setMinimumWidth(540)
        form = QFormLayout(right_group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(12)

        self.inp_fio = QLineEdit()
        set_hint_and_tooltip(self.inp_fio, "Иванов Иван Иванович")

        dob_widget = QWidget()
        dob_widget.setFixedHeight(28)
        dob_hbox = QHBoxLayout(dob_widget)
        dob_hbox.setContentsMargins(0, 0, 0, 0)
        dob_hbox.setSpacing(6)

        compact_style = "padding: 2px 6px; max-height: 24px;"
        self.inp_day = QLineEdit()
        self.inp_day.setStyleSheet(compact_style)
        set_hint_and_tooltip(self.inp_day, "11")
        self.inp_day.setMaxLength(2)
        self.inp_day.setFixedWidth(55)

        self.inp_month = QLineEdit()
        self.inp_month.setStyleSheet(compact_style)
        set_hint_and_tooltip(self.inp_month, "06")
        self.inp_month.setMaxLength(2)
        self.inp_month.setFixedWidth(55)

        self.inp_year = QLineEdit()
        self.inp_year.setStyleSheet(compact_style)
        set_hint_and_tooltip(self.inp_year, "2000")
        self.inp_year.setMaxLength(4)
        self.inp_year.setFixedWidth(75)

        dob_hbox.addWidget(QLabel("Число:"))
        dob_hbox.addWidget(self.inp_day)
        dob_hbox.addWidget(QLabel("Месяц:"))
        dob_hbox.addWidget(self.inp_month)
        dob_hbox.addWidget(QLabel("Год:"))
        dob_hbox.addWidget(self.inp_year)
        dob_hbox.addStretch()

        self.inp_citizenship = QLineEdit()
        set_hint_and_tooltip(self.inp_citizenship, "Российская Федерация", is_default_value=True)

        self.inp_address = QPlainTextEdit()
        set_hint_and_tooltip(
            self.inp_address,
            "Российская Федерация, 196605, г. Санкт-Петербург, г. Пушкин, ул. Ленинградская, д. 93/3, кв. 54",
        )
        self.inp_address.setFixedHeight(52)

        pass_row1 = QWidget()
        pass_hbox1 = QHBoxLayout(pass_row1)
        pass_hbox1.setContentsMargins(0, 0, 0, 0)
        pass_hbox1.setSpacing(8)

        self.inp_pass_series = QLineEdit()
        set_hint_and_tooltip(self.inp_pass_series, "9999 (4 цифры)")
        self.inp_pass_series.setMaxLength(4)
        self.inp_pass_series.setFixedWidth(110)
        self.inp_pass_series.textEdited.connect(
            lambda txt: self._digits_only_filter(self.inp_pass_series, txt, 4)
        )

        self.inp_pass_number = QLineEdit()
        set_hint_and_tooltip(self.inp_pass_number, "999999 (6 цифр)")
        self.inp_pass_number.setMaxLength(6)
        self.inp_pass_number.setFixedWidth(135)
        self.inp_pass_number.textEdited.connect(
            lambda txt: self._digits_only_filter(self.inp_pass_number, txt, 6)
        )

        pass_hbox1.addWidget(QLabel("Серия:"))
        pass_hbox1.addWidget(self.inp_pass_series)
        pass_hbox1.addWidget(QLabel("Номер:"))
        pass_hbox1.addWidget(self.inp_pass_number)
        pass_hbox1.addStretch()

        self.inp_pass_issued_by = QLineEdit()
        set_hint_and_tooltip(
            self.inp_pass_issued_by,
            "УВД Пушкинского района по г. Санкт-Петербург",
        )

        pass_row2 = QWidget()
        pass_hbox2 = QHBoxLayout(pass_row2)
        pass_hbox2.setContentsMargins(0, 0, 0, 0)
        pass_hbox2.setSpacing(8)

        self.inp_pass_date = QLineEdit()
        set_hint_and_tooltip(self.inp_pass_date, "11.01.2006")
        self.inp_pass_date.setFixedWidth(120)
        self.inp_pass_date.textEdited.connect(self._on_pass_date_edited)

        self.inp_pass_code = QLineEdit()
        set_hint_and_tooltip(self.inp_pass_code, "999-999")
        self.inp_pass_code.setFixedWidth(110)
        self.inp_pass_code.textEdited.connect(self._on_pass_code_edited)

        pass_hbox2.addWidget(QLabel("Дата:"))
        pass_hbox2.addWidget(self.inp_pass_date)
        pass_hbox2.addWidget(QLabel("Код:"))
        pass_hbox2.addWidget(self.inp_pass_code)
        pass_hbox2.addStretch()

        self.inp_phone = QLineEdit()
        set_hint_and_tooltip(self.inp_phone, "8 (999) 999-99-99")
        self.inp_phone.textEdited.connect(self._on_phone_edited)

        self.inp_email = QLineEdit()
        set_hint_and_tooltip(self.inp_email, "author@mail.ru")

        self.inp_contribution = QLineEdit()
        set_hint_and_tooltip(
            self.inp_contribution,
            "Разработка программного обеспечения.",
            is_default_value=True,
        )

        sig_png_widget = QWidget()
        sig_png_hbox = QHBoxLayout(sig_png_widget)
        sig_png_hbox.setContentsMargins(0, 0, 0, 0)
        sig_png_hbox.setSpacing(8)
        btn_pick_png = QPushButton("🖼 Выбрать скан подписи (.png)")
        btn_pick_png.setObjectName("SecondaryBtn")
        btn_pick_png.setToolTip(
            "Выберите изображение подписи (.png на прозрачном фоне) для автоматической вставки в Согласия"
        )
        btn_pick_png.clicked.connect(self._pick_signature_png)
        self.btn_pick_png = btn_pick_png
        btn_clear_png = QPushButton("×")
        self.btn_clear_png = btn_clear_png
        btn_clear_png.setObjectName("SecondaryBtn")
        btn_clear_png.setFixedWidth(32)
        btn_clear_png.setToolTip("Открепить картинку подписи .png")
        btn_clear_png.clicked.connect(self._clear_signature_png)
        self.lbl_sig_png = ElidedLabel("Не выбрана")
        self.lbl_sig_png.setStyleSheet("color: #64748B;")
        sig_png_hbox.addWidget(btn_pick_png, stretch=1)
        sig_png_hbox.addWidget(self.lbl_sig_png)
        sig_png_hbox.addWidget(btn_clear_png)

        sig_pfx_widget = QWidget()
        sig_pfx_hbox = QHBoxLayout(sig_pfx_widget)
        sig_pfx_hbox.setContentsMargins(0, 0, 0, 0)
        sig_pfx_hbox.setSpacing(8)
        btn_pick_pfx = QPushButton("🔐 Добавить ЭЦП (.pfx)")
        btn_pick_pfx.setObjectName("SecondaryBtn")
        btn_pick_pfx.setToolTip(
            "Сохраняет PFX в карточке автора. Электронное подписание документов не выполняется."
        )
        btn_pick_pfx.clicked.connect(self._pick_signature_pfx)
        self.btn_pick_pfx = btn_pick_pfx
        btn_clear_pfx = QPushButton("×")
        self.btn_clear_pfx = btn_clear_pfx
        btn_clear_pfx.setObjectName("SecondaryBtn")
        btn_clear_pfx.setFixedWidth(32)
        btn_clear_pfx.setToolTip("Открепить файл .pfx")
        btn_clear_pfx.clicked.connect(self._clear_signature_pfx)
        self.lbl_sig_pfx = ElidedLabel("Не выбрана")
        self.lbl_sig_pfx.setStyleSheet("color: #64748B;")
        sig_pfx_hbox.addWidget(btn_pick_pfx, stretch=1)
        sig_pfx_hbox.addWidget(self.lbl_sig_pfx)
        sig_pfx_hbox.addWidget(btn_clear_pfx)

        form.addRow("Полное ФИО:*", self.inp_fio)
        form.addRow("Дата рождения:*", dob_widget)
        form.addRow("Гражданство:*", self.inp_citizenship)
        form.addRow("Место жительства:*", self.inp_address)
        form.addRow("Паспорт (серия/№):*", pass_row1)
        form.addRow("Паспорт выдан:*", self.inp_pass_issued_by)
        form.addRow("Выдача / Код:*", pass_row2)
        form.addRow("Телефон (Заявл.):", self.inp_phone)
        form.addRow("Email (Заявл.):", self.inp_email)
        form.addRow("Творческий вклад:*", self.inp_contribution)
        form.addRow("Подпись-картинка:", sig_png_widget)
        form.addRow("Файл ЭЦП (.pfx):", sig_pfx_widget)

        self.btn_save_author = QPushButton("💾 Сохранить автора в БД")
        self.btn_save_author.clicked.connect(self._save_author_from_form)
        form.addRow("", self.btn_save_author)

        splitter.addWidget(left_group)
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.Shape.NoFrame)
        form_scroll.setMinimumWidth(560)
        form_scroll.setWidget(right_group)
        splitter.addWidget(form_scroll)
        splitter.setSizes([660, 660])
        layout.addWidget(splitter)

    # --------------------------------------------------------------------------
    # Вкладка 2: Загрузка модулей, назначение авторов галочками и генерация
    # --------------------------------------------------------------------------
    def _build_modules_tab(self):
        vbox = QVBoxLayout(self.tab_modules)
        vbox.setContentsMargins(14, 14, 14, 14)
        vbox.setSpacing(12)

        toolbar_group = QGroupBox("Шаг 1. Загрузка списка модулей (программ для ЭВМ)")
        tb_layout = QHBoxLayout(toolbar_group)

        btn_load_folder = QPushButton("📁 Загрузить из папки с листингами (.docx / .pdf)")
        btn_load_folder.clicked.connect(self._load_modules_from_folder)

        btn_load_excel = QPushButton("📊 Импорт списка из Excel (.xlsx)")
        btn_load_excel.setObjectName("SecondaryBtn")
        btn_load_excel.clicked.connect(self._load_modules_from_excel)

        btn_add_manual = QPushButton("➕ Добавить модуль вручную")
        btn_add_manual.setObjectName("SecondaryBtn")
        btn_add_manual.clicked.connect(self._add_module_manually)

        btn_del_module = QPushButton("🗑 Удалить выбранный")
        btn_del_module.setObjectName("SecondaryBtn")
        btn_del_module.clicked.connect(self._remove_selected_module)

        btn_clear_modules = QPushButton("Очистить все")
        btn_clear_modules.setObjectName("DangerBtn")
        btn_clear_modules.clicked.connect(self._clear_all_modules)

        tb_layout.addWidget(btn_load_folder)
        tb_layout.addWidget(btn_load_excel)
        tb_layout.addWidget(btn_add_manual)
        tb_layout.addStretch()
        tb_layout.addWidget(btn_del_module)
        tb_layout.addWidget(btn_clear_modules)
        vbox.addWidget(toolbar_group)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        mod_group = QGroupBox("Список модулей программы (название и число листов можно редактировать двойным кликом)")
        mod_vbox = QVBoxLayout(mod_group)
        self.tbl_modules = QTableWidget(0, 5)
        self.tbl_modules.setHorizontalHeaderLabels([
            "№", "Название программы (модуля)", "Год", "Листов листинга", "Выбранные авторы"
        ])
        self.tbl_modules.verticalHeader().setVisible(False)
        self.tbl_modules.setAlternatingRowColors(True)
        self.tbl_modules.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl_modules.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.tbl_modules.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.tbl_modules.setColumnWidth(0, 45)
        self.tbl_modules.setColumnWidth(2, 65)
        self.tbl_modules.setColumnWidth(3, 125)
        self.tbl_modules.itemSelectionChanged.connect(self._on_module_selected)
        self.tbl_modules.itemChanged.connect(self._on_module_cell_edited)
        mod_vbox.addWidget(self.tbl_modules)
        splitter.addWidget(mod_group)

        auth_sel_group = QGroupBox("Шаг 2. Выбор авторов для модуля (галочками)")
        auth_sel_vbox = QVBoxLayout(auth_sel_group)

        self.lbl_current_module = QLabel("Выберите модуль в таблице слева")
        self.lbl_current_module.setStyleSheet("color: #1E3A8A; font-weight: 700;")
        auth_sel_vbox.addWidget(self.lbl_current_module)

        self.lst_module_authors = QListWidget()
        self.lst_module_authors.itemChanged.connect(self._on_author_checkbox_toggled)
        auth_sel_vbox.addWidget(self.lst_module_authors)

        main_auth_row = QHBoxLayout()
        main_auth_row.addWidget(QLabel("Основной автор (для Заявления):"))
        self.cmb_main_author = QComboBox()
        self.cmb_main_author.currentIndexChanged.connect(self._on_main_author_changed)
        main_auth_row.addWidget(self.cmb_main_author, stretch=1)
        auth_sel_vbox.addLayout(main_auth_row)

        btn_apply_all = QPushButton("☑️ Применить выбранных авторов ко ВСЕМ модулям")
        btn_apply_all.setObjectName("SecondaryBtn")
        btn_apply_all.clicked.connect(self._apply_authors_to_all_modules)
        auth_sel_vbox.addWidget(btn_apply_all)

        splitter.addWidget(auth_sel_group)
        splitter.setSizes([780, 500])
        vbox.addWidget(splitter, stretch=1)

        gen_group = QGroupBox("Шаг 3. Генерация пакетов документов")
        gen_grid = QGridLayout(gen_group)

        self.inp_templates_dir = QLineEdit(str(self.paths.templates_dir))
        btn_browse_tpl = QPushButton("Обзор...")
        btn_browse_tpl.setObjectName("SecondaryBtn")
        btn_browse_tpl.clicked.connect(self._browse_templates_dir)

        self.inp_output_dir = QLineEdit(str(self.paths.output_dir))
        btn_browse_out = QPushButton("Обзор...")
        btn_browse_out.setObjectName("SecondaryBtn")
        btn_browse_out.clicked.connect(self._browse_output_dir)

        # Поле выбора даты подписи документов (по умолчанию текущая дата, формат ДД.ММ.ГГГГ)
        options_row = QWidget()
        opt_hbox = QHBoxLayout(options_row)
        opt_hbox.setContentsMargins(0, 0, 0, 0)
        opt_hbox.setSpacing(16)

        self.date_sign = QDateEdit(QDate.currentDate())
        self.date_sign.setCalendarPopup(True)
        self.date_sign.setDisplayFormat("dd.MM.yyyy")
        self.date_sign.setFixedWidth(130)
        self.date_sign.setToolTip("Дата подписи документов (в согласия подставится в формате ДД.ММ.ГГГГг.)")

        self.chk_convert_pdf = QCheckBox(
            "Также создать PDF через Microsoft Word"
        )
        self.chk_convert_pdf.setToolTip("Конвертировать созданные DOCX в PDF с уже вставленными PNG-подписями авторов")

        opt_hbox.addWidget(QLabel("📅 Дата подписи:"))
        opt_hbox.addWidget(self.date_sign)
        opt_hbox.addWidget(self.chk_convert_pdf)
        opt_hbox.addStretch()

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.lbl_status = QLabel("Готово к работе.")
        self.lbl_status.setStyleSheet("color: #475569; font-weight: 600;")

        self.btn_generate = QPushButton("🚀 Создать документы")
        self.btn_generate.setObjectName("SuccessBtn")
        self.btn_generate.clicked.connect(self._run_generation)

        gen_grid.addWidget(QLabel("Папка шаблонов:"), 0, 0)
        gen_grid.addWidget(self.inp_templates_dir, 0, 1)
        gen_grid.addWidget(btn_browse_tpl, 0, 2)

        gen_grid.addWidget(QLabel("Папка сохранения:"), 1, 0)
        gen_grid.addWidget(self.inp_output_dir, 1, 1)
        gen_grid.addWidget(btn_browse_out, 1, 2)

        gen_grid.addWidget(options_row, 2, 0, 1, 2)
        gen_grid.addWidget(self.btn_generate, 2, 2, 2, 1)
        gen_grid.addWidget(self.progress_bar, 3, 0, 1, 2)
        gen_grid.addWidget(self.lbl_status, 4, 0, 1, 3)

        vbox.addWidget(gen_group)
        self._generation_controls = [
            self.tab_authors, toolbar_group, mod_group, auth_sel_group,
            self.inp_templates_dir, btn_browse_tpl, self.inp_output_dir,
            btn_browse_out, options_row, self.btn_generate,
        ]

    # --------------------------------------------------------------------------
    # Вспомогательные обработчики ввода и подписей
    # --------------------------------------------------------------------------
    def _digits_only_filter(self, line_edit: QLineEdit, text: str, max_len: int):
        digits = "".join(ch for ch in text if ch.isdigit())[:max_len]
        if digits != text:
            line_edit.blockSignals(True)
            line_edit.setText(digits)
            line_edit.setCursorPosition(len(digits))
            line_edit.blockSignals(False)

    def _on_pass_date_edited(self, text: str):
        formatted = format_date_ddmmyyyy(text)
        if formatted != text:
            self.inp_pass_date.blockSignals(True)
            self.inp_pass_date.setText(formatted)
            self.inp_pass_date.setCursorPosition(len(formatted))
            self.inp_pass_date.blockSignals(False)

    def _on_pass_code_edited(self, text: str):
        formatted = format_department_code(text)
        if formatted != text:
            self.inp_pass_code.blockSignals(True)
            self.inp_pass_code.setText(formatted)
            self.inp_pass_code.setCursorPosition(len(formatted))
            self.inp_pass_code.blockSignals(False)

    def _on_phone_edited(self, text: str):
        formatted = format_russian_phone(text)
        if formatted != text:
            self.inp_phone.blockSignals(True)
            self.inp_phone.setText(formatted)
            self.inp_phone.setCursorPosition(len(formatted))
            self.inp_phone.blockSignals(False)

    def _pick_signature_png(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите изображение подписи (.png)", "", "PNG Images (*.png)"
        )
        if file_path:
            self.current_sig_png_path = file_path
            self.lbl_sig_png.setText(f"✅ {Path(file_path).name}")
            self.lbl_sig_png.setToolTip(file_path)
            self.lbl_sig_png.setStyleSheet("color: #16A34A; font-weight: 600;")

    def _clear_signature_png(self):
        self.current_sig_png_path = ""
        self.lbl_sig_png.setText("Не выбрана")
        self.lbl_sig_png.setToolTip("")
        self.lbl_sig_png.setStyleSheet("color: #64748B;")

    def _pick_signature_pfx(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл сертификата ЭЦП (.pfx)", "", "PFX Certificates (*.pfx *.p12)"
        )
        if file_path:
            self.current_sig_pfx_path = file_path
            self.lbl_sig_pfx.setText(f"🔐 {Path(file_path).name}")
            self.lbl_sig_pfx.setToolTip(file_path)
            self.lbl_sig_pfx.setStyleSheet("color: #2563EB; font-weight: 600;")

    def _clear_signature_pfx(self):
        self.current_sig_pfx_path = ""
        self.lbl_sig_pfx.setText("Не выбрана")
        self.lbl_sig_pfx.setToolTip("")
        self.lbl_sig_pfx.setStyleSheet("color: #64748B;")

    # --------------------------------------------------------------------------
    # Работа с таблицей и формой авторов
    # --------------------------------------------------------------------------
    def _refresh_authors_ui(self):
        self.authors_cache = self.db.get_all_authors()
        self.tbl_authors.setRowCount(len(self.authors_cache))
        for r_idx, a in enumerate(self.authors_cache):
            dob_str = f"{a['birth_day']}.{a['birth_month']}.{a['birth_year']}"
            has_png = bool(a.get("signature_png") and self.db.resolve_signature_path(a["signature_png"]).exists())
            has_pfx = bool(a.get("signature_pfx") and self.db.resolve_signature_path(a["signature_pfx"]).exists())
            sig_status = "🖼 PNG" if has_png else ""
            if has_pfx:
                sig_status = f"{sig_status} + 🔐 PFX".strip(" +")
            if not sig_status:
                sig_status = "—"

            vals = [
                str(a["id"]),
                a["full_name"],
                dob_str,
                a["phone"],
                sig_status,
                a["contribution"],
            ]
            for c_idx, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setData(Qt.ItemDataRole.UserRole, a["id"])
                self.tbl_authors.setItem(r_idx, c_idx, item)

        self._rebuild_author_checkbox_list()
        self._refresh_modules_table()

    def _parse_legacy_passport_if_needed(self, author: dict):
        series = author.get("pass_series", "")
        number = author.get("pass_number", "")
        issued_by = author.get("pass_issued_by", "")
        issue_date = author.get("pass_issue_date", "")
        code = author.get("pass_code", "")

        raw = author.get("passport_info", "")
        if not any([series, number, issued_by, issue_date, code]) and raw:
            m_ser = re.search(r"серия\s*(\d{4})", raw)
            m_num = re.search(r"№\s*(\d{5,6})", raw)
            m_by = re.search(r"выдан\s+(.+?),\s*дата выдачи", raw)
            m_dt = re.search(r"дата выдачи:\s*([\d\.]{8,10})", raw)
            m_cd = re.search(r"код подразделения:\s*([\d\-]{6,7})", raw)
            if m_ser:
                series = m_ser.group(1)
            if m_num:
                number = m_num.group(1)
            if m_by:
                issued_by = m_by.group(1).strip()
            if m_dt:
                issue_date = m_dt.group(1).strip()
            if m_cd:
                code = m_cd.group(1).strip()

        return series, number, issued_by, issue_date, code

    def _on_author_table_selected(self):
        items = self.tbl_authors.selectedItems()
        if not items:
            return
        author_id = items[0].data(Qt.ItemDataRole.UserRole)
        author = next((a for a in self.authors_cache if a["id"] == author_id), None)
        if not author:
            return

        self.selected_author_id_for_edit = author["id"]
        self.inp_fio.setText(author["full_name"])
        self.inp_day.setText(author["birth_day"])
        self.inp_month.setText(author["birth_month"])
        self.inp_year.setText(author["birth_year"])
        self.inp_citizenship.setText(author["citizenship"])
        self.inp_address.setPlainText(author["address"])

        series, number, issued_by, issue_date, code = self._parse_legacy_passport_if_needed(author)
        self.inp_pass_series.setText(series)
        self.inp_pass_number.setText(number)
        self.inp_pass_issued_by.setText(issued_by)
        self.inp_pass_date.setText(issue_date)
        self.inp_pass_code.setText(code)

        self.inp_phone.setText(author["phone"])
        self.inp_email.setText(author["email"])
        self.inp_contribution.setText(author["contribution"])

        self.current_sig_png_path = author.get("signature_png", "")
        self.lbl_sig_png.setToolTip(str(self.db.resolve_signature_path(self.current_sig_png_path)) if self.current_sig_png_path else "")
        if self.current_sig_png_path and self.db.resolve_signature_path(self.current_sig_png_path).exists():
            self.lbl_sig_png.setText(f"✅ {self.db.resolve_signature_path(self.current_sig_png_path).name}")
            self.lbl_sig_png.setStyleSheet("color: #16A34A; font-weight: 600;")
        else:
            self.lbl_sig_png.setText("Файл недоступен (ссылка сохранена)" if self.current_sig_png_path else "Не выбрана")
            self.lbl_sig_png.setStyleSheet("color: #64748B;")

        self.current_sig_pfx_path = author.get("signature_pfx", "")
        self.lbl_sig_pfx.setToolTip(str(self.db.resolve_signature_path(self.current_sig_pfx_path)) if self.current_sig_pfx_path else "")
        if self.current_sig_pfx_path and self.db.resolve_signature_path(self.current_sig_pfx_path).exists():
            self.lbl_sig_pfx.setText(f"🔐 {self.db.resolve_signature_path(self.current_sig_pfx_path).name}")
            self.lbl_sig_pfx.setStyleSheet("color: #2563EB; font-weight: 600;")
        else:
            self.lbl_sig_pfx.setText("Файл недоступен (ссылка сохранена)" if self.current_sig_pfx_path else "Не выбрана")
            self.lbl_sig_pfx.setStyleSheet("color: #64748B;")

        self.btn_save_author.setText(f"💾 Обновить данные автора (ID {author['id']})")

    def _clear_author_form(self):
        self.selected_author_id_for_edit = None
        self.tbl_authors.clearSelection()
        self.inp_fio.clear()
        self.inp_day.clear()
        self.inp_month.clear()
        self.inp_year.clear()
        self.inp_citizenship.setText("Российская Федерация")
        self.inp_address.clear()
        self.inp_pass_series.clear()
        self.inp_pass_number.clear()
        self.inp_pass_issued_by.clear()
        self.inp_pass_date.clear()
        self.inp_pass_code.clear()
        self.inp_phone.clear()
        self.inp_email.clear()
        self.inp_contribution.setText("Разработка программного обеспечения.")
        self._clear_signature_png()
        self._clear_signature_pfx()
        self.btn_save_author.setText("💾 Сохранить нового автора в БД")

    def _save_author_from_form(self):
        if not self.inp_fio.text().strip() or not self.inp_day.text().strip():
            QMessageBox.warning(self, "Проверка данных", "Заполните как минимум ФИО и дату рождения автора.")
            return

        data = {
            "full_name": self.inp_fio.text(),
            "birth_day": self.inp_day.text(),
            "birth_month": self.inp_month.text(),
            "birth_year": self.inp_year.text(),
            "citizenship": self.inp_citizenship.text(),
            "address": self.inp_address.toPlainText(),
            "pass_series": self.inp_pass_series.text(),
            "pass_number": self.inp_pass_number.text(),
            "pass_issued_by": self.inp_pass_issued_by.text(),
            "pass_issue_date": self.inp_pass_date.text(),
            "pass_code": self.inp_pass_code.text(),
            "phone": self.inp_phone.text(),
            "email": self.inp_email.text(),
            "contribution": self.inp_contribution.text(),
            "signature_png": self.current_sig_png_path,
            "signature_pfx": self.current_sig_pfx_path,
        }

        if self.selected_author_id_for_edit is not None:
            self.db.update_author(self.selected_author_id_for_edit, data)
        else:
            self.db.add_author(data)

        self._clear_author_form()
        self._refresh_authors_ui()

    def _delete_selected_author(self):
        if self.selected_author_id_for_edit is None:
            QMessageBox.information(self, "Удаление", "Сначала выберите автора в таблице.")
            return
        reply = QMessageBox.question(
            self,
            "Подтверждение",
            "Удалить выбранного автора из базы данных?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.db.delete_author(self.selected_author_id_for_edit)
            self._clear_author_form()
            self._refresh_authors_ui()

    # --------------------------------------------------------------------------
    # Логика работы с модулями и галочками авторов (Вкладка 2)
    # --------------------------------------------------------------------------
    def _default_author_ids(self) -> list[int]:
        return [a["id"] for a in self.authors_cache]

    def _load_modules_from_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку с файлами листингов (.docx / .pdf)")
        if not folder:
            return
        scanned = scan_listings_directory(Path(folder))
        if not scanned:
            QMessageBox.information(
                self, "Папка пуста", "В выбранной папке не найдено файлов листинга (.docx, .pdf, .txt, .py)."
            )
            return

        existing_names = {m["program_name"].strip().lower() for m in self.modules_data}
        default_ids = self._default_author_ids()
        added_count = 0

        for m in scanned:
            norm_name = m["program_name"].strip().lower()
            if norm_name in existing_names:
                continue
            existing_names.add(norm_name)
            m["author_ids"] = list(default_ids)
            m["main_author_id"] = default_ids[0] if default_ids else None
            self.modules_data.append(m)
            added_count += 1

        self._refresh_modules_table()
        if added_count > 0:
            self.tbl_modules.selectRow(len(self.modules_data) - added_count)

    def _load_modules_from_excel(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Выберите файл Excel со списком модулей", "", "Excel Files (*.xlsx)"
        )
        if not file_path:
            return
        imported = import_modules_from_excel(Path(file_path))
        existing_names = {m["program_name"].strip().lower() for m in self.modules_data}
        default_ids = self._default_author_ids()

        for m in imported:
            norm_name = m["program_name"].strip().lower()
            if norm_name in existing_names:
                continue
            existing_names.add(norm_name)
            m["author_ids"] = list(default_ids)
            m["main_author_id"] = default_ids[0] if default_ids else None
            self.modules_data.append(m)

        self._refresh_modules_table()
        if self.modules_data:
            self.tbl_modules.selectRow(0)

    def _add_module_manually(self):
        default_ids = self._default_author_ids()
        next_num = len(self.modules_data) + 1
        self.modules_data.append({
            "program_name": f"Новый модуль {next_num}",
            "creation_year": "2026",
            "listing_pages": 10,
            "source_file": "",
            "listing_text": "",
            "author_ids": list(default_ids),
            "main_author_id": default_ids[0] if default_ids else None,
        })
        self._refresh_modules_table()
        self.tbl_modules.selectRow(len(self.modules_data) - 1)

    def _remove_selected_module(self):
        row = self.tbl_modules.currentRow()
        if row < 0 or row >= len(self.modules_data):
            return
        self.modules_data.pop(row)
        self._refresh_modules_table()

    def _clear_all_modules(self):
        self.modules_data.clear()
        self._refresh_modules_table()
        self.lbl_current_module.setText("Выберите модуль в таблице слева")

    def _refresh_modules_table(self):
        self.updating_ui = True
        current_row = self.tbl_modules.currentRow()
        self.tbl_modules.setRowCount(len(self.modules_data))

        authors_by_id = {a["id"]: a for a in self.authors_cache}

        for r_idx, mod in enumerate(self.modules_data):
            it_num = QTableWidgetItem(str(r_idx + 1))
            it_num.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            it_num.setFlags(it_num.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tbl_modules.setItem(r_idx, 0, it_num)

            it_name = QTableWidgetItem(mod["program_name"])
            self.tbl_modules.setItem(r_idx, 1, it_name)

            it_year = QTableWidgetItem(str(mod["creation_year"]))
            it_year.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tbl_modules.setItem(r_idx, 2, it_year)

            it_pages = QTableWidgetItem(str(mod["listing_pages"]))
            it_pages.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tbl_modules.setItem(r_idx, 3, it_pages)

            valid_ids = [aid for aid in mod["author_ids"] if aid in authors_by_id]
            mod["author_ids"] = valid_ids
            names_str = ", ".join(authors_by_id[aid]["short_name_end"] for aid in valid_ids) or "Не выбраны!"
            it_auth = QTableWidgetItem(names_str)
            it_auth.setFlags(it_auth.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tbl_modules.setItem(r_idx, 4, it_auth)

        self.updating_ui = False
        if 0 <= current_row < len(self.modules_data):
            self.tbl_modules.selectRow(current_row)

    def _on_module_cell_edited(self, item: QTableWidgetItem):
        if self.updating_ui:
            return
        row = item.row()
        col = item.column()
        if not (0 <= row < len(self.modules_data)):
            return

        if col == 1:
            self.modules_data[row]["program_name"] = item.text().strip()
        elif col == 2:
            self.modules_data[row]["creation_year"] = item.text().strip() or "2026"
        elif col == 3:
            try:
                self.modules_data[row]["listing_pages"] = max(1, int(item.text().strip()))
            except ValueError:
                self.modules_data[row]["listing_pages"] = 10

    def _rebuild_author_checkbox_list(self):
        self.updating_ui = True
        self.lst_module_authors.clear()
        for a in self.authors_cache:
            item = QListWidgetItem(f"{a['full_name']}  ({a['contribution']})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, a["id"])
            self.lst_module_authors.addItem(item)
        self.updating_ui = False

    def _on_module_selected(self):
        row = self.tbl_modules.currentRow()
        if row < 0 or row >= len(self.modules_data):
            return
        mod = self.modules_data[row]
        self.lbl_current_module.setText(f"Модуль №{row + 1}: «{mod['program_name']}»")

        self.updating_ui = True
        selected_ids = set(mod["author_ids"])
        for i in range(self.lst_module_authors.count()):
            item = self.lst_module_authors.item(i)
            aid = item.data(Qt.ItemDataRole.UserRole)
            item.setCheckState(
                Qt.CheckState.Checked if aid in selected_ids else Qt.CheckState.Unchecked
            )
        self._update_main_author_combo(mod)
        self.updating_ui = False

    def _update_main_author_combo(self, mod: dict):
        self.cmb_main_author.blockSignals(True)
        self.cmb_main_author.clear()
        authors_by_id = {a["id"]: a for a in self.authors_cache}
        for aid in mod["author_ids"]:
            if aid in authors_by_id:
                self.cmb_main_author.addItem(authors_by_id[aid]["full_name"], aid)

        if mod.get("main_author_id") in mod["author_ids"]:
            idx = self.cmb_main_author.findData(mod["main_author_id"])
            if idx >= 0:
                self.cmb_main_author.setCurrentIndex(idx)
        elif mod["author_ids"]:
            mod["main_author_id"] = mod["author_ids"][0]
            self.cmb_main_author.setCurrentIndex(0)
        self.cmb_main_author.blockSignals(False)

    def _on_author_checkbox_toggled(self, _item: QListWidgetItem):
        if self.updating_ui:
            return
        row = self.tbl_modules.currentRow()
        if row < 0 or row >= len(self.modules_data):
            return

        checked_ids = []
        for i in range(self.lst_module_authors.count()):
            it = self.lst_module_authors.item(i)
            if it.checkState() == Qt.CheckState.Checked:
                checked_ids.append(it.data(Qt.ItemDataRole.UserRole))

        mod = self.modules_data[row]
        mod["author_ids"] = checked_ids
        if mod.get("main_author_id") not in checked_ids:
            mod["main_author_id"] = checked_ids[0] if checked_ids else None

        self._update_main_author_combo(mod)
        self._refresh_modules_table()

    def _on_main_author_changed(self, _idx: int):
        if self.updating_ui:
            return
        row = self.tbl_modules.currentRow()
        if row < 0 or row >= len(self.modules_data):
            return
        aid = self.cmb_main_author.currentData()
        if aid is not None:
            self.modules_data[row]["main_author_id"] = aid

    def _apply_authors_to_all_modules(self):
        row = self.tbl_modules.currentRow()
        if row < 0 or row >= len(self.modules_data):
            return
        source_mod = self.modules_data[row]
        for m in self.modules_data:
            m["author_ids"] = list(source_mod["author_ids"])
            m["main_author_id"] = source_mod.get("main_author_id")
        self._refresh_modules_table()
        QMessageBox.information(
            self, "Готово", "Выбранный состав авторов применен ко всем модулям в списке."
        )

    def _browse_templates_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Выберите папку с шаблонами DOCX")
        if d:
            self.inp_templates_dir.setText(d)

    def _browse_output_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Выберите папку для сохранения готовых документов")
        if d:
            self.inp_output_dir.setText(d)

    def _run_generation(self):
        if self.worker is not None:
            return
        if not self.modules_data:
            QMessageBox.warning(self, "Нет модулей", "Сначала добавьте или загрузите хотя бы один модуль программы.")
            return

        authors_by_id = {}
        for author in self.authors_cache:
            prepared = dict(author)
            for key in ("signature_png", "signature_pfx"):
                if prepared.get(key):
                    prepared[key] = str(self.db.resolve_signature_path(prepared[key]))
            authors_by_id[prepared["id"]] = prepared
        tasks = []
        for idx, mod in enumerate(self.modules_data, start=1):
            selected_authors = [authors_by_id[aid] for aid in mod["author_ids"] if aid in authors_by_id]
            if not selected_authors:
                QMessageBox.warning(
                    self,
                    "Не выбраны авторы",
                    f"Для модуля №{idx} («{mod['program_name']}») не выбран ни один автор.",
                )
                return

            main_id = mod.get("main_author_id")
            main_author = next((a for a in selected_authors if a["id"] == main_id), selected_authors[0])
            ordered_authors = [main_author] + [a for a in selected_authors if a["id"] != main_author["id"]]

            tasks.append({
                "module": mod,
                "authors": ordered_authors,
                "main_author": main_author,
            })

        if not self.inp_templates_dir.text().strip() or not self.inp_output_dir.text().strip():
            QMessageBox.warning(self, "Пути", "Укажите папку шаблонов и папку сохранения.")
            return
        templates_dir = absolute_path(self.inp_templates_dir.text().strip(), self.paths.project_dir)
        output_dir = absolute_path(self.inp_output_dir.text().strip(), self.paths.data_dir)
        if not templates_dir.is_dir():
            QMessageBox.warning(self, "Шаблоны", f"Папка шаблонов не найдена:\n{templates_dir}")
            return
        sign_date_str = f"{self.date_sign.date().toString('dd.MM.yyyy')}г."

        self._set_generation_busy(True)
        self._generation_outcome = None
        self.progress_bar.setValue(0)

        self.worker = GenerationWorker(
            tasks=tasks,
            templates_dir=templates_dir,
            output_dir=output_dir,
            sign_date=sign_date_str,
            convert_pdf=self.chk_convert_pdf.isChecked(),
        )
        self.worker.progress.connect(self._on_gen_progress)
        self.worker.finished_ok.connect(self._on_gen_finished)
        self.worker.failed.connect(self._on_gen_failed)
        self.worker.module_completed.connect(self._on_module_generated)
        self.worker.finished.connect(self._on_worker_stopped)
        self.worker.setParent(self)
        self.worker.start()

    def _on_gen_progress(self, percent: int, message: str):
        self.progress_bar.setValue(percent)
        self.lbl_status.setText(message)

    def _on_gen_finished(self, count: int, out_path: str):
        self._generation_outcome = ("ok", count, out_path)

    def _on_gen_failed(self, err_msg: str):
        self._generation_outcome = ("error", err_msg)
        self.lbl_status.setText(f"Ошибка: {err_msg}")

    def _on_module_generated(self, row: int, pages: int):
        if 0 <= row < len(self.modules_data):
            self.modules_data[row]["listing_pages"] = pages
            self._refresh_modules_table()

    def _set_generation_busy(self, busy: bool):
        for control in self._generation_controls:
            control.setEnabled(not busy)

    def _on_worker_stopped(self):
        # QThread.finished приходит после завершения run(): окно уже можно закрыть.
        worker = self.worker
        self.worker = None
        if worker is not None:
            worker.deleteLater()
        self._set_generation_busy(False)
        self._refresh_modules_table()
        result = self._generation_outcome
        if result and not self.suppress_notifications:
            if result[0] == "ok":
                _, count, out_path = result
                open_folder_in_explorer(out_path)
                QMessageBox.information(
                    self, "Успешно выполнено",
                    f"Создано комплектов документов (по 6 файлов): {count} шт.\n\nПапка с результатами:\n{out_path}",
                )
            else:
                QMessageBox.critical(self, "Ошибка при генерации", f"Произошла ошибка:\n{result[1]}")
        self.generation_stopped.emit()


class AutomatDocsWindow(QMainWindow):
    def __init__(self, icon_path: Path | None = None, paths: AppPaths | None = None):
        super().__init__()
        self.resize(1600, 940)
        self.setStyleSheet(MODERN_QSS)
        self._close_when_finished = False

        central = QWidget()
        central.setObjectName("AppSurface")
        layout = QHBoxLayout(central)
        layout.setContentsMargins(8, 8, 12, 8)
        layout.setSpacing(16)
        self.setCentralWidget(central)
        self.sidebar = Sidebar()
        self.navigation = self.sidebar.navigation
        layout.addWidget(self.sidebar)
        workspace = QVBoxLayout()
        workspace.setSpacing(12)
        self.banner = HeroBanner()
        workspace.addWidget(self.banner)
        self.stack = QStackedWidget()
        # Экран создаётся один раз: переключение сохраняет формы и таблицы.
        self.documents = DocumentsWidget(paths or resolve_paths())
        self.stack.addWidget(self.documents)
        for section in SECTIONS[1:]:
            self.stack.addWidget(PlannedSection(section))
        self.navigation.currentRowChanged.connect(self._on_section_changed)
        self.navigation.setCurrentRow(0)
        self.documents.generation_stopped.connect(self._on_generation_stopped)
        workspace.addWidget(self.stack, stretch=1)
        layout.addLayout(workspace, stretch=1)

    def _on_section_changed(self, index: int):
        if not 0 <= index < len(SECTIONS):
            return
        section = SECTIONS[index]
        self.stack.setCurrentIndex(index)
        self.banner.set_section(section)
        self.sidebar.set_section(section)
        self.setWindowTitle(f"{APP_NAME} (Octopus) — {section.caption}")
        self.setWindowIcon(section_icon(section.key))

    def closeEvent(self, event):
        if self.documents.worker is not None:
            if not self._close_when_finished:
                answer = QMessageBox.question(
                    self, "Выполняется генерация",
                    "Документы ещё создаются. Дождаться завершения и закрыть окно?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer == QMessageBox.StandardButton.Yes:
                    self._close_when_finished = True
                    self.documents.suppress_notifications = True
                    self.documents.lbl_status.setText("Окно закроется после завершения генерации…")
            # Вложенный цикл диалога мог уже обработать QThread.finished.
            if self._close_when_finished and self.documents.worker is None:
                event.accept()
                return
            event.ignore()
            return
        event.accept()

    def _on_generation_stopped(self):
        if self._close_when_finished:
            QTimer.singleShot(0, self.close)


def resolve_icon_path(paths: AppPaths | None = None) -> Path:
    """
    Ищет icon.ico:
    1) Внутри распакованного архива PyInstaller (sys._MEIPASS)
    2) В папке рядом с самим .exe файлом (sys.executable)
    3) В рабочей папке проекта
    """
    candidates = []
    if paths is not None:
        candidates.extend((paths.resource_dir / "icon.ico", paths.project_dir / "icon.ico"))
    if hasattr(sys, "_MEIPASS"):
        candidates.append(Path(sys._MEIPASS) / "icon.ico")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "icon.ico")
    candidates.append(Path(__file__).resolve().parent / "icon.ico")

    for p in candidates:
        if p.exists():
            return p
    return Path(__file__).resolve().parent / "icon.ico"


def select_startup_paths(options) -> AppPaths | None:
    values = {key: getattr(options, key) for key in ("project_dir", "data_dir", "db_path", "templates_dir")}
    while True:
        try:
            return resolve_paths(**values)
        except AmbiguousDatabaseError as exc:
            message = QMessageBox()
            message.setIcon(QMessageBox.Icon.Warning)
            message.setWindowTitle("Выбор базы авторов")
            message.setText(str(exc))
            message.setTextFormat(Qt.TextFormat.PlainText)
            choose = message.addButton("Выбрать базу…", QMessageBox.ButtonRole.AcceptRole)
            message.addButton(QMessageBox.StandardButton.Cancel)
            message.exec()
            if message.clickedButton() != choose:
                return None
            filename, _ = QFileDialog.getOpenFileName(
                None, "Выберите существующую базу авторов", str(exc.candidates[0].parent),
                "Базы SQLite (*.db *.sqlite *.sqlite3);;Все файлы (*)",
            )
            if not filename:
                return None
            values["db_path"] = filename
        except (OSError, ValueError) as exc:
            QMessageBox.critical(None, "Пути приложения", str(exc))
            return None


def main():
    parser = argparse.ArgumentParser(description="Осьминог (Octopus) — работа с документами")
    parser.add_argument("--project-dir", help="Папка проекта с существующими данными и шаблонами")
    parser.add_argument("--data-dir", help="Папка данных: database/, signatures/, output/")
    parser.add_argument("--db-path", help="Путь к существующей базе SQLite (без переноса)")
    parser.add_argument("--templates-dir", help="Папка редактируемых шаблонов DOCX")
    options, qt_args = parser.parse_known_args()
    # Защита для режима --noconsole в PyInstaller (чтобы ни одна библиотека не падала при выводе в консоль)
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")

    # Привязка AppUserModelID для отображения иконки на панели задач Windows
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("automat.docs.app.1.0")
        except Exception:
            pass

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationDisplayName(f"{APP_NAME} (Octopus)")
    app.setStyleSheet(MODERN_QSS)

    paths = select_startup_paths(options)
    if paths is None:
        return 1
    icon_path = resolve_icon_path(paths)
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    try:
        window = AutomatDocsWindow(icon_path=icon_path, paths=paths)
    except Exception as exc:
        QMessageBox.critical(None, "Не удалось открыть данные", f"База: {paths.db_path}\n{exc}")
        return 1
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
