"""Оболочка «Осьминог»: оформление и описание экранов, без обработки файлов."""
from dataclasses import dataclass

from PyQt6.QtCore import QByteArray, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)


APP_NAME = "Осьминог"


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    caption: str
    subtitle: str
    stage: int
    features: tuple[tuple[str, str], ...] = ()


SECTIONS = (
    Section("documents", "Документы", "Автоматизация документов",
            "Подготовка комплектов документов для регистрации программ ЭВМ", 1),
    Section("recognition", "Распознавание", "Распознавание документов",
            "Распознавание текста из PDF и Word и сохранение в общий документ", 2, (
                ("Документы и сканы", "Пакетная работа с документами и страницами со сканами."),
                ("Русский и английский", "Локальное распознавание текста на вашем компьютере."),
                ("Общий результат", "Текст для копирования и общий Word-файл с границами документов."),
            )),
    Section("text", "Текст", "Написание и перевод текста",
            "Написание, редактирование и перевод текста в одном рабочем пространстве", 3, (
                ("Написание", "Подготовка текста по вашему заданию."),
                ("Перевод", "Перевод с возможностью проверить и исправить результат."),
                ("Ваш выбор", "Локальная модель или настроенный облачный провайдер."),
            )),
    Section("summary", "Конспект", "Конспекты и суммаризация",
            "Краткие конспекты видео, сайтов, документов и изображений", 4, (
                ("Разные источники", "Материалы сайтов, документов и видео."),
                ("Главное по теме", "Краткое резюме и ключевые тезисы."),
                ("Связь с оригиналом", "Ссылки на источник, таймкоды и экспорт в Word."),
            )),
    Section("sources", "Источники", "Поиск и проверка источников",
            "Поиск публикаций, отбор материалов и проверка ссылок", 5, (
                ("Поиск по теме", "Академические публикации и их метаданные."),
                ("Отбор источников", "Проверка происхождения ссылок и доступности текста."),
                ("Работа с материалами", "План, черновик и список использованной литературы."),
            )),
    Section("code", "Код и документы", "Работа с кодом",
            "Подготовка исходников и комплекта документов для программы ЭВМ", 6, (
                ("Выбор исходников", "Явный выбор файлов для комплекта."),
                ("Предпросмотр", "Проверка реферата и листинга перед генерацией."),
                ("Комплект документов", "Передача принятых исходников в генератор."),
            )),
    Section("sorting", "Сортировка файлов", "Порядок в файлах",
            "Локальная сортировка файлов с предварительным просмотром перемещений", 7, (
                ("Выбор папки", "Обработка только выбранной вами папки."),
                ("План перемещений", "Предпросмотр действий и конфликтов имён."),
                ("Применение", "Перемещение выбранных файлов после проверки плана."),
            )),
)


_GLYPHS = {
    "documents": '<path d="M7 3h7l4 4v14H5V3z M13 3v5h5 M8 12h7 M8 16h7"/>',
    "recognition": '<path d="M8 3H3v5 M16 3h5v5 M21 16v5h-5 M8 21H3v-5 M5 12h14 M9 8h6v8H9z"/>',
    "text": '<path d="M4 5h16 M12 5v15 M8 20h8 M4 9V5 M20 9V5"/>',
    "summary": '<rect x="4" y="3" width="16" height="18" rx="3"/><path d="m9 7 6 4-6 4z M8 18h8"/>',
    "sources": '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6 M7 8h6 M7 11h4"/>',
    "code": '<path d="m8 6-6 6 6 6 M16 6l6 6-6 6 M14 3l-4 18"/>',
    "sorting": '<path d="M3 7h7l2 2h9v11H3z M4 7V4h6l2 3 M8 13h8 M8 16h5"/>',
    "folder": '<path d="M3 7h7l2 2h9v11H3z M3 7V4h7l2 3"/>',
    "octopus": '<path d="M6 12V9a6 6 0 0 1 12 0v3 M6 12c-6 0-5 8-1 7 3 0 1-5 4-5 M18 12c6 0 5 8 1 7-3 0-1-5-4-5 M9 14v5a3 3 0 0 0 6 0v-5"/><circle cx="9" cy="9" r=".8"/><circle cx="15" cy="9" r=".8"/>',
}


def glyph_pixmap(key: str, size: int, color: str = "#FFFFFF") -> QPixmap:
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><g fill="none" stroke="{color}" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">{_GLYPHS[key]}</g></svg>'
    # Высокая плотность сохраняет чёткость значков при масштабировании Windows.
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    QSvgRenderer(QByteArray(svg.encode())).render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return pixmap


def section_icon(key: str) -> QIcon:
    return QIcon(glyph_pixmap(key, 64, "#3972FF"))


class HeroBanner(QFrame):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(124)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(30, 15, 28, 15)
        text = QVBoxLayout()
        text.setSpacing(5)
        text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.title = QLabel()
        self.title.setObjectName("HeroTitle")
        self.subtitle = QLabel()
        self.subtitle.setObjectName("HeroSubtitle")
        self.subtitle.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.subtitle)
        layout.addLayout(text, 1)
        self.art = QLabel()
        self.art.setFixedSize(88, 88)
        self.art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.art)

    def set_section(self, section: Section):
        self.title.setText(section.title)
        self.subtitle.setText(section.subtitle)
        self.art.setPixmap(glyph_pixmap(section.key, 74))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, QColor("#487FF8"))
        gradient.setColorAt(.48, QColor("#326CF0"))
        gradient.setColorAt(1, QColor("#8992FF"))
        painter.setPen(QPen(QColor("#FFFFFF"), 1.4))
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, 18, 18)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 18, 18)
        painter.setClipPath(clip)
        w, h = rect.width(), rect.height()
        for offset, alpha in ((0, 35), (70, 28)):
            wave = QPainterPath(QPointF(w * .43, h))
            wave.cubicTo(w * .7, h + offset, w * .67, -h * .6 + offset, w, h * .64 + offset)
            wave.lineTo(w, h)
            wave.closeSubpath()
            painter.fillPath(wave, QColor(195, 231, 255, alpha))
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1.2))
        painter.setBrush(QColor(255, 255, 255, 35))
        painter.drawRoundedRect(QRectF(w - 155, 15, 118, h + 20), 18, 18)


class Sidebar(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(220)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 23, 12, 18)
        layout.setSpacing(12)
        self.avatar = QLabel()
        self.avatar.setObjectName("SectionAvatar")
        self.avatar.setFixedSize(72, 72)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.avatar, alignment=Qt.AlignmentFlag.AlignHCenter)
        brand = QLabel(APP_NAME)
        brand.setObjectName("BrandTitle")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(brand)
        english = QLabel("O C T O P U S")
        english.setObjectName("BrandEnglish")
        english.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(english)
        self.caption = QLabel()
        self.caption.setObjectName("SidebarCaption")
        self.caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.caption.setWordWrap(True)
        self.caption.setMinimumHeight(44)
        layout.addWidget(self.caption)
        self.navigation = QListWidget()
        self.navigation.setObjectName("Navigation")
        self.navigation.setAccessibleName("Разделы приложения")
        self.navigation.setIconSize(QSize(23, 23))
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for section in SECTIONS:
            item = QListWidgetItem(QIcon(glyph_pixmap(section.key, 28)), section.title)
            item.setToolTip(section.caption)
            self.navigation.addItem(item)
        layout.addWidget(self.navigation, 1)
        footer = QLabel("Локальное приложение\nДанные на вашем компьютере")
        footer.setObjectName("SidebarFooter")
        footer.setWordWrap(True)
        layout.addWidget(footer)

    def set_section(self, section: Section):
        self.avatar.setPixmap(glyph_pixmap(section.key, 45))
        self.avatar.setAccessibleName(section.caption)
        self.caption.setText(section.caption)


class PlannedSection(QScrollArea):
    """Доступный для просмотра дизайн будущего раздела; обработчиков пока нет."""
    def __init__(self, section: Section):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("PlannedContent")
        self.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(5, 8, 5, 5)
        layout.setSpacing(18)
        card = QFrame()
        card.setObjectName("PreviewCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(36, 30, 36, 30)
        card_layout.setSpacing(16)
        icon = QLabel()
        icon.setPixmap(glyph_pixmap(section.key, 64, "#3972FF"))
        card_layout.addWidget(icon)
        badge = QLabel(f"ЭТАП {section.stage} · ЗАПЛАНИРОВАНО")
        badge.setObjectName("PreviewBadge")
        card_layout.addWidget(badge)
        title = QLabel(section.caption)
        title.setObjectName("PreviewTitle")
        title.setWordWrap(True)
        card_layout.addWidget(title)
        self.notice = QLabel(
            "Пока доступен макет раздела. Обработка документов появится на этапе 2."
            if section.stage == 2 else
            f"Пока доступен макет раздела. Функции появятся на этапе {section.stage}."
        )
        self.notice.setObjectName("PreviewNotice")
        self.notice.setWordWrap(True)
        card_layout.addWidget(self.notice)
        layout.addWidget(card)
        for number, (heading, description) in enumerate(section.features, 1):
            feature = QFrame()
            feature.setObjectName("FeatureCard")
            row = QHBoxLayout(feature)
            row.setContentsMargins(22, 18, 22, 18)
            number_label = QLabel(f"0{number}")
            number_label.setObjectName("FeatureNumber")
            number_label.setFixedWidth(48)
            row.addWidget(number_label)
            text = QVBoxLayout()
            name = QLabel(heading)
            name.setObjectName("FeatureTitle")
            text.addWidget(name)
            detail = QLabel(description)
            detail.setWordWrap(True)
            text.addWidget(detail)
            row.addLayout(text, 1)
            layout.addWidget(feature)
        layout.addStretch()


class ElidedLabel(QLabel):
    """Длинное имя подписи не сдвигает крестик и не расширяет карточку."""
    def __init__(self, text):
        super().__init__(text)
        self.setMinimumWidth(76)
        self.setMaximumWidth(140)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideMiddle, self.width()))


MODERN_QSS = """
QMainWindow { background: #DCE5FF; }
QWidget { color: #172653; font-family: 'Segoe UI', sans-serif; font-size: 13px; }
QWidget#AppSurface {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #EDF2FF,stop:1 #CDD9FF);
}
QLabel { background: transparent; }
QToolTip { background: #172D66; color: white; border: 0; padding: 7px; }
QFrame#Sidebar {
    background: qlineargradient(x1:0,y1:0,x2:.8,y2:1,stop:0 #122859,stop:.5 #1C3E8F,stop:1 #354FB2);
    border: 1px solid #F1F5FF; border-radius: 19px;
}
QLabel#SectionAvatar {
    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #8DAAFF,stop:1 #476FEA);
    border: 1px solid #ACBEFF; border-radius: 19px;
}
QLabel#BrandTitle { color: white; font-size: 25px; font-weight: 700; }
QLabel#BrandEnglish { color: #B4C9FF; font-size: 10px; font-weight: 600; }
QLabel#SidebarCaption { color: #D7E4FF; font-size: 13px; }
QLabel#SidebarFooter { color: #CCDBFF; border-top: 1px solid #5268AB; padding: 16px 4px 0; font-size: 11px; }
QListWidget#Navigation { background: transparent; border: none; outline: none; color: #EFF5FF; }
QListWidget#Navigation::item { padding: 13px 9px; margin: 3px 0; border: 1px solid transparent; border-radius: 11px; }
QListWidget#Navigation::item:hover { background: #2D52A6; }
QListWidget#Navigation::item:selected {
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #4999FF,stop:1 #2865F6);
    border: 1px solid #96C5FF; color: white;
}
QLabel#HeroTitle { color: white; font-size: 32px; font-weight: 700; }
QLabel#HeroSubtitle { color: #EFF6FF; font-size: 15px; }
QFrame#DatabaseBar { background: #E7EDFF; border: 1px solid #FAFCFF; border-radius: 13px; }
QLabel#DatabaseStatus { background: #DCF5EA; color: #19844F; padding: 8px 14px; border: 1px solid #ECFFF3; border-radius: 10px; font-weight: 600; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: #DCE3FF; color: #273B71; border: 1px solid #F7FAFF; border-radius: 11px; padding: 12px 20px; margin: 3px 8px 9px 0; font-weight: 600; }
QTabBar::tab:selected { background: #FAFCFF; color: #2058EE; border-bottom: 3px solid #3B6DFF; }
QTabBar::tab:hover:!selected { background: #EAF0FF; }
QGroupBox { background: #EAF0FF; border: 1px solid #FFFFFF; border-radius: 17px; margin-top: 18px; padding: 18px 12px 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; left: 18px; padding: 0 7px; color: #1C3278; }
QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QDateEdit {
    background: #F8FAFF; color: #182B59; border: 1px solid #BDCDF5; border-radius: 8px; padding: 6px 9px;
    selection-background-color: #3E73F7; selection-color: white;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QDateEdit:focus { border: 1px solid #5186FA; background: white; }
QLineEdit:read-only { background: #E4EBFF; }
QTableWidget, QListWidget { background: #F6F8FF; alternate-background-color: #EBF0FF; border: 1px solid #C4D1F4; border-radius: 11px; gridline-color: #D7E1FC; selection-background-color: #CCDDFF; selection-color: #172653; outline: none; }
QHeaderView::section { background: #E0E8FF; border: none; border-right: 1px solid #D0DCFA; border-bottom: 1px solid #C9D6F8; padding: 10px 6px; color: #273965; font-weight: 600; }
QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #368BFF,stop:1 #2554EE); border: 1px solid #8AB7FF; border-radius: 9px; color: white; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #3277FF; border: 1px solid #B7D8FF; }
QPushButton:pressed { background: #214ED9; }
QPushButton#SecondaryBtn { background: #F1F5FF; color: #254BA5; border: 1px solid #C1D1F7; }
QPushButton#SecondaryBtn:hover { background: #E2EBFF; border: 1px solid #8FB0F5; }
QPushButton#DangerBtn { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #FF7595,stop:1 #EF3C66); border: 1px solid #FFB9CA; }
QPushButton#DangerBtn:hover { background: #ED3C68; }
QPushButton#SuccessBtn { font-size: 14px; padding: 12px 20px; border-radius: 12px; }
QPushButton:disabled { background: #D8E0F3; color: #8D9CBE; border-color: #D8E0F3; }
QProgressBar { border: 1px solid #C0D0F7; border-radius: 7px; background: #F4F7FF; text-align: center; min-height: 22px; }
QProgressBar::chunk { background: #5586FC; border-radius: 6px; }
QSplitter::handle { background: transparent; width: 9px; }
QScrollArea { border: none; background: transparent; }
QWidget#PlannedContent { background: transparent; }
QFrame#PreviewCard, QFrame#FeatureCard { background: #F3F6FF; border: 1px solid white; border-radius: 17px; }
QLabel#PreviewBadge { color: #4E73D7; font-size: 11px; font-weight: 700; }
QLabel#PreviewTitle { color: #1D367A; font-size: 25px; font-weight: 700; }
QLabel#PreviewNotice { color: #5E709C; font-size: 14px; }
QLabel#FeatureNumber { color: #6A8DE6; font-size: 25px; font-weight: 600; }
QLabel#FeatureTitle { color: #233E7D; font-size: 15px; font-weight: 600; }
"""
