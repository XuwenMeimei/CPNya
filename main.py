import sys
import os
import psutil
import webbrowser
from PySide6.QtWidgets import (
    QApplication, QLabel, QVBoxLayout, QWidget,
    QDialog, QCheckBox, QComboBox, QPushButton, QLabel as QLab,
    QSystemTrayIcon, QMenu
)
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QIcon, QAction, QCursor
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pynvml import *

# --------- 单实例检测 ---------
def is_another_instance_running(key="OverlaySingleton"):  
    socket = QLocalSocket()
    socket.connectToServer(key)
    if socket.waitForConnected(100):
        return True
    socket.close()
    return False


def create_instance_lock(key="OverlaySingleton"):
    server = QLocalServer()
    if not server.listen(key):
        QLocalServer.removeServer(key)
        server.listen(key)
    return server

# --------- 工具函数 ---------
def lerp_color(c1, c2, t):
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )

def color_smooth_gradient(percent: float) -> str:
    percent = max(0, min(percent, 100)) / 100.0
    green, yellow, orange, red = (0,255,0),(255,255,0),(255,165,0),(255,0,0)
    if percent <= 1/3:
        t = percent/(1/3)
        r,g,b = lerp_color(green,yellow,t)
    elif percent <= 2/3:
        t = (percent-1/3)/(1/3)
        r,g,b = lerp_color(yellow,orange,t)
    else:
        t = (percent-2/3)/(1/3)
        r,g,b = lerp_color(orange,red,t)
    return f"#{r:02X}{g:02X}{b:02X}"

def temperature_color(temp: float, min_temp=30, max_temp=90) -> str:
    t = (temp-min_temp)/(max_temp-min_temp)
    t = max(0.0, min(1.0, t))
    green, yellow, orange, red = (0,255,0),(255,255,0),(255,165,0),(255,0,0)
    if t <= 1/3:
        r,g,b = lerp_color(green,yellow,t/(1/3))
    elif t <= 2/3:
        r,g,b = lerp_color(yellow,orange,(t-1/3)/(1/3))
    else:
        r,g,b = lerp_color(orange,red,(t-2/3)/(1/3))
    return f"#{r:02X}{g:02X}{b:02X}"

# --------- 设置窗口 ---------
class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("设置")
        self.setFixedSize(300, 300)
        self.setStyleSheet("""
            QDialog { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffe6f2, stop:1 #fffbf7); border-radius: 15px; }
            QCheckBox { spacing: 10px; font-size: 14px; }
            QCheckBox::indicator { width: 20px; height: 20px; }
            QComboBox { padding: 4px; font-size: 14px; border: 2px solid #ffb3c6; border-radius: 8px; }
            QPushButton { padding: 6px 12px; font-size: 14px; border-radius: 12px; background-color: #ff99b3; }
            QPushButton:hover { background-color: #ff80a1; }
        """)

        # 选项
        self.cpu_checkbox = QCheckBox("显示 CPU 信息")
        self.cpu_checkbox.setChecked(True)
        self.percore_checkbox = QCheckBox("显示 每核 使用率")
        self.percore_checkbox.setChecked(True)
        self.memory_checkbox = QCheckBox("显示 内存 信息")
        self.memory_checkbox.setChecked(True)
        self.gpu_checkbox = QCheckBox("显示 GPU 信息")
        self.gpu_checkbox.setChecked(True)
        self.temp_checkbox = QCheckBox("显示 GPU 温度")
        self.temp_checkbox.setChecked(True)
        self.vram_checkbox = QCheckBox("显示 VRAM 信息")
        self.vram_checkbox.setChecked(True)

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["GB", "MB"])

        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)

        layout = QVBoxLayout()
        for w in [self.cpu_checkbox, self.percore_checkbox, self.memory_checkbox, self.gpu_checkbox, self.temp_checkbox, self.vram_checkbox]:
            layout.addWidget(w)
        layout.addSpacing(10)
        layout.addWidget(QLab("内存 单位:"))
        layout.addWidget(self.unit_combo)
        layout.addStretch()
        layout.addWidget(ok_btn, alignment=Qt.AlignCenter)
        self.setLayout(layout)

    def get_settings(self):
        return {
            'show_cpu': self.cpu_checkbox.isChecked(),
            'show_percore': self.percore_checkbox.isChecked(),
            'show_memory': self.memory_checkbox.isChecked(),
            'show_gpu': self.gpu_checkbox.isChecked(),
            'show_temp': self.temp_checkbox.isChecked(),
            'show_vram': self.vram_checkbox.isChecked(),
            'memory_unit': self.unit_combo.currentText()
        }

# --------- 资源路径 ---------
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --------- 托盘图标 ---------
class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, app):
        icon_path = resource_path("icon.ico")
        super().__init__(QIcon(icon_path), parent=app)
        self.setToolTip("CPNya")

        self.menu = QMenu()
        self.menu.setStyleSheet("""
            QMenu {
                background-color: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #e6e6e6);
                border: 1px solid #aaa;
                border-radius: 8px;
                padding: 5px;
            }
            QMenu::item {
                padding: 8px 24px;
                margin: 2px 0;
                border-radius: 4px;
                font-size: 13px;
                color: #333;
            }
            QMenu::item:selected {
                background-color: #3498db;
                color: white;
            }
            QMenu::separator {
                height: 1px;
                background: #bbb;
                margin: 4px 0;
            }
        """)
        # GitHub
        action_github = QAction("GitHub", self.menu)
        action_github.triggered.connect(lambda: webbrowser.open("https://github.com/XuwenMeimei/CPNya"))
        self.menu.addAction(action_github)

        self.menu.addSeparator()
        # 退出
        quit_action = QAction("退出程序", self.menu)
        quit_action.triggered.connect(app.quit)
        self.menu.addAction(quit_action)

        self.setContextMenu(self.menu)
        self.activated.connect(self.on_tray_activated)
        self.show()


    def on_tray_activated(self, reason):
        from PySide6.QtWidgets import QSystemTrayIcon
        if reason == QSystemTrayIcon.Context:
            self.menu.popup(QCursor.pos())

# --------- 叠加窗口 ---------
class OverlayWindow(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        # 提升进程优先级
        try:
            p = psutil.Process(os.getpid())
            p.nice(psutil.REALTIME_PRIORITY_CLASS if hasattr(psutil,'REALTIME_PRIORITY_CLASS') else -20)
        except:
            pass

        # GPU 初始化
        try:
            nvmlInit()
            self.gpu_handle = nvmlDeviceGetHandleByIndex(0)
            self.gpu_available = True
        except:
            self.gpu_available = False

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            def set_window_always_on_top(hwnd):
                SWP_NOSIZE = 0x0001
                SWP_NOMOVE = 0x0002
                SWP_NOACTIVATE = 0x0010
                HWND_TOPMOST = -1
                ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            hwnd = self.winId().__int__()
            set_window_always_on_top(hwnd)

        font = QFont("Segoe UI", 10)
        font.setStyleStrategy(QFont.PreferAntialias)
        self.label = QLabel()
        self.label.setFont(font)
        self.label.setTextFormat(Qt.RichText)
        self.label.setStyleSheet("""
            QLabel { color: white; background-color: rgba(0, 0, 0, 128);
            border-radius: 12px; padding: 10px; font-family: 'Comic Sans MS'; }
        """)
        self.label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        layout = QVBoxLayout()
        layout.addWidget(self.label)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(layout)

        self.hidden = False
        self.move(10, 10)
        self.orig_pos = self.pos()

        # 平移动画
        self.anim = QPropertyAnimation(self, b'pos')
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)

        # 定时更新信息
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_info)
        self.timer.start(1000)

        # 鼠标监测
        self.mouse_timer = QTimer(self)
        self.mouse_timer.timeout.connect(self.check_mouse)
        self.mouse_timer.start(100)

        self.update_info()
        self.show()

    def update_info(self):
        parts = []
        if self.settings['show_cpu']:
            total = psutil.cpu_percent()
            cpu_str = f"CPU: <span style='color:{color_smooth_gradient(total)};'>{total:.0f}%</span>"
            if self.settings['show_percore']:
                per = psutil.cpu_percent(percpu=True)
                pcs = " ".join(f"<span style='color:{color_smooth_gradient(p)};'>{p:.0f}%</span>" for p in per)
                cpu_str += f" <span style='color:white;'>(</span>{pcs}<span style='color:white;'>)</span>"
            parts.append(cpu_str)

        if self.settings['show_memory']:
            mem = psutil.virtual_memory()
            unit = self.settings['memory_unit']
            used = mem.used / (1024**3) if unit == 'GB' else mem.used / (1024**2)
            tot = mem.total / (1024**3) if unit == 'GB' else mem.total / (1024**2)
            parts.append(
                f"Memory: <span style='color:{color_smooth_gradient(mem.percent)};'>{used:.1f}/{tot:.1f} {unit}</span>"
                f" <span style='color:white;'>(</span><span style='color:{color_smooth_gradient(mem.percent)};'>{mem.percent:.0f}%</span><span style='color:white;'>)</span>"
            )

        if self.settings['show_gpu']:
            if self.gpu_available:
                u = nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                gpu_str = f"GPU: <span style='color:{color_smooth_gradient(u)};'>{u}%</span>"
                if self.settings['show_temp']:
                    t = nvmlDeviceGetTemperature(self.gpu_handle, NVML_TEMPERATURE_GPU)
                    gpu_str += f" <span style='color:white;'>(</span><span style='color:{temperature_color(t)};'>{t}°C</span><span style='color:white;'>)</span>"
                parts.append(gpu_str)
            else:
                parts.append("GPU: <span style='color:gray;'>N/A</span>")

        if self.settings['show_vram']:
            if self.gpu_available:
                mi = nvmlDeviceGetMemoryInfo(self.gpu_handle)
                pct = int(mi.used / mi.total * 100)
                parts.append(
                    f"VRAM: <span style='color:{color_smooth_gradient(pct)};'>{int(mi.used/1024**2)}/{int(mi.total/1024**2)} MB</span>"
                    f" <span style='color:white;'>(</span><span style='color:{color_smooth_gradient(pct)};'>{pct}%</span><span style='color:white;'>)</span>"
                )
            else:
                parts.append("VRAM: <span style='color:gray;'>N/A</span>")

        self.label.setText("<br>".join(parts))
        self.label.adjustSize()
        self.adjustSize()

    def check_mouse(self):
        pos = QCursor.pos()
        margin = 10
        rect = QRect(self.orig_pos.x(), self.orig_pos.y(), self.width(), self.height())
        rect = rect.adjusted(-margin, -margin, margin, margin)
        if not self.hidden and rect.contains(pos):
            self.anim.stop()
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(QPoint(-self.width(), self.orig_pos.y()))
            self.anim.start()
            self.hidden = True
        elif self.hidden and not rect.contains(pos):
            self.anim.stop()
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(self.orig_pos)
            self.anim.start()
            self.hidden = False

    def closeEvent(self, event):
        if getattr(self, 'gpu_available', False):
            nvmlShutdown()
        event.accept()

# --------- 程序入口 ---------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if is_another_instance_running():
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "提示", "程序已在运行中！")
        sys.exit(0)

    instance_lock = create_instance_lock()

    dlg = SettingsDialog()
    if dlg.exec() == QDialog.Accepted:
        cfg = dlg.get_settings()
        win = OverlayWindow(cfg)
        tray = SystemTrayIcon(app)
        sys.exit(app.exec())
