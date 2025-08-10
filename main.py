import sys
import os
import time
import psutil
import json
import webbrowser
import shutil
import darkdetect
from PySide6.QtWidgets import (
    QApplication, QLabel, QVBoxLayout, QWidget,
    QDialog, QCheckBox, QComboBox, QPushButton, QLabel as QLab,
    QSystemTrayIcon, QMenu, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, QRect, QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QFont, QIcon, QCursor, QAction
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from pynvml import *
import subprocess
import ctypes
import threading

# --------- 配置管理 ---------
user32 = ctypes.windll.user32
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", "."), "CPNya")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PRESENTMON_NAME = "PresentMon.exe"
PRESENTMON_DEST = os.path.join(CONFIG_DIR, PRESENTMON_NAME)

dwm_mode = False


if sys.platform == "win32":
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

def get_foreground_window_pid():
    hwnd = user32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value

def ensure_presentmon_in_appdata():
    if os.path.exists(PRESENTMON_DEST):
        return True
    src = resource_path(PRESENTMON_NAME)
    if os.path.exists(src):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            shutil.copy2(src, PRESENTMON_DEST)
        except Exception:
            pass
        return True


class PresentMonRunner:
    def __init__(self):
        self.process = None
        self.running = False
        self.current_fps = 0
        self.check_timeout_running = False

    def start(self, pid):
        ok = ensure_presentmon_in_appdata()
        if not ok:
            print("[ERROR] PresentMon.exe 未找到，FPS 功能不可用")
            return
        print(f"[DEBUG] PRESENTMON_DEST: {PRESENTMON_DEST}")
        self.stop()
        self.process = subprocess.Popen(
            [PRESENTMON_DEST, '--stop_existing_session', '--process_id', str(pid), '--output_stdout'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            creationflags=CREATE_NO_WINDOW
        )
        self.running = True
        self.check_timeout_running = True
        self.last_output_time = time.time()
        threading.Thread(target=self._read_output, daemon=True).start()
        threading.Thread(target=self._check_timeout, daemon=True).start()

    def _read_output(self):
        try:
            for line in self.process.stdout:
                self.last_output_time = time.time()
                fields = line.strip().split(",")
                if len(fields) > 10:
                    frame_time_str = fields[10]
                    try:
                        frame_time = float(frame_time_str)
                        self.current_fps = 1000 / frame_time
                    except ValueError:
                        pass
        except Exception as e:
            print(f"[ERROR] 读取 PresentMon 输出出错: {e}")

    def _check_timeout(self):
        while self.check_timeout_running:
            if time.time() - self.last_output_time > 1:
                print("[INFO] PID模式超时，切换到dwm.exe进程模式")
                self.stop()
                self.process = subprocess.Popen(
                    [PRESENTMON_DEST, '--stop_existing_session', '--process_name', 'dwm.exe', '--output_stdout'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    creationflags=CREATE_NO_WINDOW
                )
                global dwm_mode
                dwm_mode = True
                self.last_output_time = time.time()
                threading.Thread(target=self._read_output, daemon=True).start()
                break
            time.sleep(0.1)

    def stop(self):
        global dwm_mode
        dwm_mode = False
        self.check_timeout_running = False
        if self.process and self.running:
            self.process.terminate()
            self.process.wait()
            self.running = False

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

def load_config():
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    # 默认项
    cfg.setdefault('show_cpu', True)
    cfg.setdefault('show_percore', True)
    cfg.setdefault('show_memory', True)
    cfg.setdefault('show_gpu', True)
    cfg.setdefault('show_temp', True)
    cfg.setdefault('show_vram', True)
    cfg.setdefault('show_fps', True)
    cfg.setdefault('memory_unit', 'GB')
    cfg.setdefault('position_preset', '左上')
    # overlay 位置
    pos = cfg.get('overlay_pos')
    if not isinstance(pos, list) or len(pos) != 2:
        cfg['overlay_pos'] = [10, 10]
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)

# --------- 工具函数 ---------
def lerp_color(c1, c2, t):
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def color_smooth_gradient(percent: float) -> str:
    p = max(0, min(percent, 100)) / 100.0
    green, yellow, orange, red = (0,255,0),(255,255,0),(255,165,0),(255,0,0)
    if p <= 1/3:
        r,g,b = lerp_color(green, yellow, p/(1/3))
    elif p <= 2/3:
        r,g,b = lerp_color(yellow, orange, (p-1/3)/(1/3))
    else:
        r,g,b = lerp_color(orange, red, (p-2/3)/(1/3))
    return f"#{r:02X}{g:02X}{b:02X}"

def color_reverse_gradient(percent: float) -> str:
    p = max(0, min(percent, 100)) / 100.0
    red, orange, yellow, green = (255,0,0),(255,165,0),(255,255,0),(0,255,0)
    if p <= 1/3:
        r,g,b = lerp_color(red, orange, p/(1/3))
    elif p <= 2/3:
        r,g,b = lerp_color(orange, yellow, (p-1/3)/(1/3))
    else:
        r,g,b = lerp_color(yellow, green, (p-2/3)/(1/3))
    return f"#{r:02X}{g:02X}{b:02X}"


def temperature_color(temp: float, min_temp=30, max_temp=90) -> str:
    t = (temp - min_temp) / (max_temp - min_temp)
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
    def __init__(self, config=None, overlay=None):
        super().__init__()
        self.overlay = overlay
        self.setWindowTitle("设置")
        self.setFixedSize(300,400)

        if darkdetect.isDark():
            #深色模式
            self.setStyleSheet("""
            QDialog { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #3a2c34, stop:1 #1f1b1e); border-radius: 15px; }
            QCheckBox { spacing: 8px; font-size: 14px; color: #ddd; }
            QCheckBox::indicator { width: 18px; height: 18px; }
            QComboBox { padding: 4px; font-size: 14px; border: 2px solid #a86479; border-radius: 8px; background-color: #4b3a42; color: #ddd; }
            QPushButton { padding: 6px 12px; font-size: 14px; border-radius: 12px; background-color: #a86479; color: #fff; }
            QPushButton:hover { background-color: #944a63; }
        """)
        else:
            #浅色模式
            self.setStyleSheet("""
            QDialog { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #ffe6f2, stop:1 #fffbf7); border-radius: 15px; }
            QCheckBox { spacing: 8px; font-size: 14px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
            QComboBox { padding: 4px; font-size: 14px; border: 2px solid #ffb3c6; border-radius: 8px; }
            QPushButton { padding: 6px 12px; font-size: 14px; border-radius: 12px; background-color: #ff99b3; }
            QPushButton:hover { background-color: #ff80a1; }
        """)

        self.cpu_checkbox    = QCheckBox("显示 CPU 信息")
        self.percore_checkbox= QCheckBox("显示 每核 使用率")
        self.memory_checkbox = QCheckBox("显示 内存 信息")
        self.gpu_checkbox    = QCheckBox("显示 GPU 信息")
        self.temp_checkbox   = QCheckBox("显示 GPU 温度")
        self.vram_checkbox   = QCheckBox("显示 VRAM 信息")
        self.fps_checkbox     = QCheckBox("显示 FPS 信息")
        self.pos_combo       = QComboBox()
        self.pos_combo.addItems(["左上", "左下", "右上", "右下"])
        self.unit_combo      = QComboBox()
        self.unit_combo.addItems(["GB", "MB"])
        self.pos_hint_label = QLabel("左下/右下 建议配合自动隐藏任务栏使用哦~")
        self.pos_hint_label.setStyleSheet("color: gray; font-size: 10pt;")

        if config:
            self.cpu_checkbox.setChecked(config.get("show_cpu", True))
            self.percore_checkbox.setChecked(config.get("show_percore", True))
            self.memory_checkbox.setChecked(config.get("show_memory", True))
            self.gpu_checkbox.setChecked(config.get("show_gpu", True))
            self.temp_checkbox.setChecked(config.get("show_temp", True))
            self.vram_checkbox.setChecked(config.get("show_vram", True))
            self.fps_checkbox.setChecked(config.get("show_fps", True))
            pos = config.get("position_preset", "左上")
            idx_pos = self.pos_combo.findText(pos)
            unit = config.get("memory_unit", "GB")
            idx = self.unit_combo.findText(unit)
            self.pos_combo.setCurrentIndex(idx_pos if idx_pos >= 0 else 0)
            self.unit_combo.setCurrentIndex(idx if idx >= 0 else 0)
        else:
            for cb in (self.cpu_checkbox, self.percore_checkbox, self.memory_checkbox,
                       self.gpu_checkbox, self.temp_checkbox, self.vram_checkbox, self.fps_checkbox):
                cb.setChecked(True)
            self.pos_combo.setCurrentIndex(0)
            self.unit_combo.setCurrentIndex(0)

        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)

        layout = QVBoxLayout()
        for w in (self.cpu_checkbox, self.percore_checkbox, self.memory_checkbox,
                  self.gpu_checkbox, self.temp_checkbox, self.vram_checkbox, self.fps_checkbox):
            layout.addWidget(w)
            w.toggled.connect(self.update_overlay_preview)
        layout.addSpacing(10)
        layout.addWidget(QLab("位置预设:"))
        layout.addWidget(self.pos_combo)
        self.pos_combo.currentTextChanged.connect(self.update_overlay_preview)
        layout.addSpacing(10)
        layout.addWidget(QLab("内存单位:"))
        layout.addWidget(self.unit_combo)
        self.unit_combo.currentTextChanged.connect(self.update_overlay_preview)
        layout.addStretch()
        layout.addWidget(self.pos_hint_label, alignment=Qt.AlignCenter)
        layout.addWidget(ok_btn, alignment=Qt.AlignCenter)
        self.setLayout(layout)
        

        # 标记对话框打开
        if self.overlay:
            self.overlay.settings_dialog_open = True

    def update_overlay_preview(self):
        if self.overlay:
            self.overlay.settings = self.get_settings()
            try:
                self.overlay.update_info(force_reposition=True)
            except TypeError:
                try:
                    self.overlay.adjust_position(force=True)
                    self.overlay.update_info()
                except Exception:
                    self.overlay.update_info()

    def accept(self):
        settings = self.get_settings()
        save_config(settings)
        if self.overlay:
            self.overlay.settings = settings
            self.overlay.settings_dialog_open = False
            try:
                self.overlay.update_info(force_reposition=True)
            except TypeError:
                try:
                    self.overlay.adjust_position(force=True)
                    self.overlay.update_info()
                except Exception:
                    self.overlay.update_info()
        super().accept()

    def reject(self):
        if self.overlay:
            self.overlay.settings = load_config()
            self.overlay.settings_dialog_open = False
        try:
            self.overlay.update_info(force_reposition=True)
        except TypeError:
            try:
                self.overlay.adjust_position(force=True)
                self.overlay.update_info()
            except Exception:
                self.overlay.update_info()
        super().reject()



    def get_settings(self):
        settings = {
            'show_cpu':     self.cpu_checkbox.isChecked(),
            'show_percore': self.percore_checkbox.isChecked(),
            'show_memory':  self.memory_checkbox.isChecked(),
            'show_gpu':     self.gpu_checkbox.isChecked(),
            'show_temp':    self.temp_checkbox.isChecked(),
            'show_vram':    self.vram_checkbox.isChecked(),
            'show_fps':     self.fps_checkbox.isChecked(),
            'memory_unit':  self.unit_combo.currentText(),
            'position_preset': self.pos_combo.currentText()
        }
        return settings

    def accept(self):
        settings = self.get_settings()
        save_config(settings)
        if self.overlay:
            self.overlay.settings = settings
            self.overlay.settings_dialog_open = False
        super().accept()

    def reject(self):
        if self.overlay:
            # 取消时重新加载配置
            self.overlay.settings = load_config()
            self.overlay.update_info()
            self.overlay.settings_dialog_open = False
        super().reject()

# --------- 叠加窗口 ---------
class OverlayWindow(QWidget):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.settings_dialog_open = False

        # 优先级
        try:
            p = psutil.Process(os.getpid())
            p.nice(psutil.REALTIME_PRIORITY_CLASS if hasattr(psutil,'REALTIME_PRIORITY_CLASS') else -20)
        except:
            pass

        # GPU 支持
        try:
            nvmlInit()
            self.gpu_handle    = nvmlDeviceGetHandleByIndex(0)
            self.gpu_available = True
        except:
            self.gpu_available = False

        # 窗口属性
        self.setWindowFlags(
            Qt.FramelessWindowHint |
            Qt.WindowStaysOnTopHint |
            Qt.Tool |
            Qt.X11BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Label
        f = QFont("Segoe UI", 10)
        f.setStyleStrategy(QFont.PreferAntialias)
        self.label = QLabel()
        self.label.setFont(f)
        self.label.setTextFormat(Qt.RichText)
        self.label.setStyleSheet("""
            QLabel { color: white; background-color: rgba(0,0,0,128);
            border-radius: 12px; padding: 10px; font-family: 'Comic Sans MS'; }
        """)

        self.label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.setContentsMargins(0,0,0,0)

        # 动画
        self.anim = QPropertyAnimation(self, b'pos')
        self.anim.setDuration(300)
        self.anim.setEasingCurve(QEasingCurve.OutCubic)
        self.hidden = False

        # 定时
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_info)
        self.timer.start(1000)

        self.mouse_timer = QTimer(self)
        self.mouse_timer.timeout.connect(self.check_mouse)
        self.mouse_timer.start(100)

        self.update_info()

        self.adjustSize()
        geo = self.frameGeometry()
        screen_geo = QApplication.primaryScreen().geometry()

        preset = self.settings.get('position_preset')
        if preset == "左上":
            geo.moveTopLeft(QPoint(10, 10))
        elif preset == "左下":
            geo.moveBottomLeft(QPoint(10, screen_geo.bottom() - 10))
        elif preset == "右上":
            geo.moveTopRight(QPoint(screen_geo.right() - 10, 10))
        elif preset == "右上":
            geo.moveTopRight(QPoint(screen_geo.right() - 10, 10))
        elif preset == "右下":
            geo.moveBottomRight(QPoint(screen_geo.right() - 10, screen_geo.bottom() - 10))
        else:
            geo.moveTopLeft(QPoint(10, 10))

        self.setGeometry(geo)
        self.orig_pos = self.pos()

        self.show()

    def adjust_position(self, force: bool = False):
        if not force:
            if getattr(self, 'hidden', False) or getattr(self, 'settings_dialog_open', False):
                return
            if hasattr(self, 'anim') and self.anim.state() == QPropertyAnimation.Running:
                return
        else:
            if hasattr(self, 'anim'):
                self.anim.stop()
            self.hidden = False
        self.adjustSize()
        geo = self.frameGeometry()
        screen_geo = QApplication.primaryScreen().geometry()

        preset = self.settings.get('position_preset')
        if preset == "左上":
            geo.moveTopLeft(QPoint(10, 10))
        elif preset == "左下":
            geo.moveBottomLeft(QPoint(10, screen_geo.bottom() - 10))
        elif preset == "右上":
            geo.moveTopRight(QPoint(screen_geo.right() - 10, 10))
        elif preset == "右下":
            geo.moveBottomRight(QPoint(screen_geo.right() - 10, screen_geo.bottom() - 10))
        else:
            geo.moveTopLeft(QPoint(10, 10))

        self.setGeometry(geo)
        self.orig_pos = self.pos()


    def update_info(self):
        parts = []
        if self.settings['show_cpu']:
            tot = psutil.cpu_percent()
            cpu_str = f"CPU: <span style='color:{color_smooth_gradient(tot)};'>{tot:.0f}%</span>"
            if self.settings['show_percore']:
                pcs = psutil.cpu_percent(percpu=True)
                pcs_str = " ".join(f"<span style='color:{color_smooth_gradient(p)};'>{p:.0f}%</span>" for p in pcs)
                cpu_str += f" (<span style='color:white;'>{pcs_str}</span>)"
            parts.append(cpu_str)

        if self.settings['show_memory']:
            m = psutil.virtual_memory()
            unit = self.settings['memory_unit']
            used = m.used / (1024**3) if unit=='GB' else m.used/(1024**2)
            totu = m.total/(1024**3) if unit=='GB' else m.total/(1024**2)
            parts.append(
                f"Memory: <span style='color:{color_smooth_gradient(m.percent)};'>{used:.1f}/{totu:.1f} {unit}</span> "
                f"(<span style='color:{color_smooth_gradient(m.percent)};'>{m.percent:.0f}%</span>)"
            )

        if self.settings['show_gpu']:
            if self.gpu_available:
                u = nvmlDeviceGetUtilizationRates(self.gpu_handle).gpu
                gpu_str = f"GPU: <span style='color:{color_smooth_gradient(u)};'>{u}%</span>"
                if self.settings['show_temp']:
                    t = nvmlDeviceGetTemperature(self.gpu_handle, NVML_TEMPERATURE_GPU)
                    gpu_str += f" (<span style='color:{temperature_color(t)};'>{t}°C</span>)"
                parts.append(gpu_str)
            else:
                parts.append("GPU: <span style='color:gray;'>N/A</span>")

        if self.settings['show_vram']:
            if self.gpu_available:
                mi = nvmlDeviceGetMemoryInfo(self.gpu_handle)
                pct = int(mi.used/mi.total*100)
                parts.append(
                    f"VRAM: <span style='color:{color_smooth_gradient(pct)};'>{int(mi.used/1024**2)}/{int(mi.total/1024**2)} MB</span> "
                    f"(<span style='color:{color_smooth_gradient(pct)};'>{pct}%</span>)"
                )
            else:
                parts.append("VRAM: <span style='color:gray;'>N/A</span>")

        if self.settings['show_fps']:
            if not hasattr(self, 'pm_runner'):
                self.pm_runner = PresentMonRunner()
                self.last_pid = None
            
            current_pid = get_foreground_window_pid()
            if current_pid != self.last_pid:
                self.pm_runner.current_fps = 0
                self.pm_runner.start(current_pid)
                self.last_pid = current_pid
            
            fps = self.pm_runner.current_fps
            fps_color = fps / 60 * 100 if fps > 0 else 0
            fps_str = f"FPS: <span style='color:{color_reverse_gradient(fps_color)};'>{fps:.0f}</span>"
            if dwm_mode is True:
                fps_str += " <span style='color:white;'>(dwm.exe)</span>"
            parts.append(fps_str)

        self.label.setText("<br>".join(parts))
        self.label.adjustSize()
        if not getattr(self, 'hidden', False) and not (hasattr(self, 'anim') and self.anim.state() == QPropertyAnimation.Running):
            self.adjust_position()
        self.adjustSize()

    def check_mouse(self):
        if self.settings_dialog_open:
            return
        pos = QCursor.pos()
        m = 10
        r = QRect(self.orig_pos, self.size()).adjusted(-m, -m, m, m)
        preset = self.settings.get('position_preset')
        if not getattr(self, 'hidden', False) and r.contains(pos):
            self.anim.stop()
            self.anim.setStartValue(self.pos())
            if preset in ("左上", "左下"):
                self.anim.setEndValue(QPoint(-self.width(), self.orig_pos.y()))
            elif preset in ("右上", "右下"):
                screen_width = QApplication.primaryScreen().geometry().width()
                self.anim.setEndValue(QPoint(screen_width, self.orig_pos.y()))
            self.anim.start()
            self.hidden = True
        elif getattr(self, 'hidden', False) and not r.contains(pos):
            self.anim.stop()
            self.anim.setStartValue(self.pos())
            self.anim.setEndValue(self.orig_pos)
            self.anim.start()
            self.hidden = False
        

    def closeEvent(self, event):
        if getattr(self, 'gpu_available', False):
            nvmlShutdown()
        if hasattr(self, 'pm_runner'):
            self.pm_runner.stop()
        event.accept()

# --------- 托盘图标 with Settings ---------
class SystemTrayIcon(QSystemTrayIcon):
    def __init__(self, app, overlay_window):
        icon_path = resource_path("icon.ico")
        super().__init__(QIcon(icon_path), parent=app)
        self.app = app
        self.overlay = overlay_window
        self.setToolTip("CPNya")

        self.menu = QMenu()
        if darkdetect.isDark():
            #深色模式
            self.menu.setStyleSheet("""
            QMenu { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #2c2c2c, stop:1 #1e1e1e);
                   border:1px solid #555; border-radius:8px; padding:5px; color:#ddd; }
            QMenu::item { padding:8px 24px; margin:2px 0; border-radius:4px; font-size:13px; color:#ddd; }
            QMenu::item:selected { background-color:#3498db; color:white; }
            QMenu::separator { height:1px; background:#444; margin:4px 0; }
        """)
        else:
            #浅色模式
            self.menu.setStyleSheet("""
            QMenu { background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 #ffffff, stop:1 #e6e6e6);
                   border:1px solid #aaa; border-radius:8px; padding:5px; }
            QMenu::item { padding:8px 24px; margin:2px 0; border-radius:4px; font-size:13px; color:#333; }
            QMenu::item:selected { background-color:#3498db; color:white; }
            QMenu::separator { height:1px; background:#bbb; margin:4px 0; }
        """)

        action_settings = QAction("设置", self.menu)
        action_settings.triggered.connect(self.open_settings)
        self.menu.addAction(action_settings)

        action_github = QAction("GitHub", self.menu)
        action_github.triggered.connect(lambda: webbrowser.open("https://github.com/XuwenMeimei/CPNya"))
        self.menu.addAction(action_github)

        self.menu.addSeparator()

        quit_action = QAction("退出程序", self.menu)
        quit_action.triggered.connect(app.quit)
        self.menu.addAction(quit_action)

        self.setContextMenu(self.menu)
        self.activated.connect(self.on_tray_activated)
        self.show()

    def open_settings(self):
        dlg = SettingsDialog(self.overlay.settings, overlay=self.overlay)
        if dlg.exec() == QDialog.Accepted:
            self.overlay.update_info()
        else:
            self.overlay.settings_dialog_open = False

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Context:
            self.menu.popup(QCursor.pos())

# --------- 资源路径 ---------
def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# --------- 程序入口 ---------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if is_another_instance_running():
        QMessageBox.warning(None, "提示", "程序已在运行中！")
        sys.exit(0)

    instance_lock = create_instance_lock()

    cfg = load_config()
    win = OverlayWindow(cfg)
    tray = SystemTrayIcon(app, win)

    sys.exit(app.exec())
