import socket
import sys
import numpy as np
import pyqtgraph.opengl as gl
import pyqtgraph as pg
from PyQt5.QtWidgets import QApplication, QLabel, QWidget, QVBoxLayout, QFrame
from PyQt5.QtCore import QTimer, Qt, QPointF
from PyQt5.QtGui import QFont, QPainter, QPen
from scipy.spatial.transform import Rotation as R
import math
import requests
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning)

# === SETUP UDP SOCKET ===
UDP_IP = "0.0.0.0"
UDP_PORT = 5005
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.setblocking(False)

# === PYQTGRAPH WINDOW SETUP ===
app = QApplication([])
view = gl.GLViewWidget()
view.setWindowTitle("Real-Time Radio Telescope Orientation")
view.setCameraPosition(distance=5)
view.setFixedSize(800, 600)
view.show()

# Add grid and axes
grid = gl.GLGridItem()
grid.scale(1, 1, 1)
view.addItem(grid)

axes = gl.GLAxisItem()
axes.setSize(1, 1, 1)
view.addItem(axes)

north_arrow = gl.GLLinePlotItem(pos=np.array([[0, 0, 0], [0, 1, 0]]), color=(0, 1, 0, 1), width=3)
view.addItem(north_arrow)

# Labels
label_n = QLabel("N")
label_e = QLabel("E")
label_az = QLabel("Az →")
label_alt = QLabel("↑ Alt")
for lbl in (label_n, label_e, label_az, label_alt):
    lbl.setStyleSheet("color: lime; font-size: 16px; font-weight: bold;")
    lbl.setParent(view)
label_n.move(400, 20)
label_e.move(750, 300)
label_az.move(650, 500)
label_alt.move(50, 500)
label_n.show(), label_e.show(), label_az.show(), label_alt.show()

# === Compass Widget ===
class CompassWidget(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(120, 120)
        self.setStyleSheet("background-color: transparent;")
        self.angle = 0

    def setAngle(self, angle):
        self.angle = angle
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        radius = min(self.width(), self.height()) // 2 - 10

        painter.setPen(QPen(Qt.white, 2))
        painter.drawEllipse(center, radius, radius)

        painter.setPen(QPen(Qt.red, 3))
        rad = math.radians(-self.angle +90)


        x = center.x() + radius * math.cos(rad)
        y = center.y() - radius * math.sin(rad)
        painter.drawLine(center, QPointF(x, y))

        painter.setPen(Qt.white)
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)
        offsets = {"N": (0, -radius + 10), "E": (radius - 15, 0),
                   "S": (0, radius - 5), "W": (-radius + 10, 0)}
        for label, (dx, dy) in offsets.items():
            painter.drawText(center.x() + dx - 5, center.y() + dy + 5, label)

        painter.end()

compass = CompassWidget(view)
compass.move(660, 460)
compass.show()

heading_label = QLabel("Heading: 0.0° N", view)
heading_label.setStyleSheet("color: white; font-size: 12px;")
heading_label.move(660, 580)
heading_label.show()

# === HUD ===
hud = QWidget(view)
hud.setGeometry(10, 10, 280, 140)
hud.setStyleSheet("background-color: rgba(0, 0, 0, 150); border-radius: 10px;")
layout = QVBoxLayout()
hud.setLayout(layout)
hud_label = QLabel()
hud_label.setStyleSheet("color: white;")
hud_label.setFont(QFont("Courier", 10))
layout.addWidget(hud_label)
hud.show()

# === TRAIL ===
trail_points = []
max_trail_length = 200
trail = gl.GLLinePlotItem(width=2, antialias=True, color=(1, 1, 0, 1))
view.addItem(trail)

# === BASE ===
def create_base(radius=0.3, height=0.2, resolution=40):
    theta = np.linspace(0, 2 * np.pi, resolution)
    x = radius * np.cos(theta)
    y = radius * np.sin(theta)
    bottom = np.stack([x, y, np.zeros_like(x)], axis=1)
    top = np.stack([x, y, np.ones_like(x) * height], axis=1)
    verts = np.vstack((bottom, top))
    faces = []
    for i in range(resolution - 1):
        faces.append([i, i + 1, i + resolution])
        faces.append([i + 1, i + 1 + resolution, i + resolution])
    faces.append([resolution - 1, 0, 2 * resolution - 1])
    faces.append([0, resolution, 2 * resolution - 1])
    return verts, np.array(faces)

base_verts, base_faces = create_base()
telescope_base = gl.GLMeshItem(vertexes=base_verts, faces=base_faces,
                               color=(0.3, 0.3, 0.3, 1), smooth=True, drawEdges=True)
view.addItem(telescope_base)

# === DISH ===
def create_dish(radius=0.8, height=0.3, resolution=50):
    theta = np.linspace(0, 2 * np.pi, resolution)
    r = np.linspace(0, radius, resolution)
    rr, tt = np.meshgrid(r, theta)
    x = rr * np.cos(tt)
    y = rr * np.sin(tt)
    z = (rr ** 2) / (radius ** 2) * height + 0.1
    verts = np.stack([x.flatten(), y.flatten(), z.flatten()], axis=1)
    faces = []
    for i in range(resolution - 1):
        for j in range(resolution - 1):
            idx = i * resolution + j
            faces.append([idx, idx + 1, idx + resolution])
            faces.append([idx + 1, idx + 1 + resolution, idx + resolution])
    return verts, np.array(faces)

dish_verts, dish_faces = create_dish()
dish = gl.GLMeshItem(vertexes=dish_verts, faces=dish_faces,
                     color=(0.6, 0.6, 1.0, 1), smooth=True, drawEdges=False)
dish.translate(0, 0, 0.2)
view.addItem(dish)

# === ARM + RECEIVER ===
arm = gl.GLLinePlotItem(pos=np.array([[0, 0, 0.2], [0, 0, 0.8]]), color=(1, 0, 0, 1), width=3)
receiver = gl.GLScatterPlotItem(pos=np.array([[0, 0, 0.8]]), color=(1, 0, 1, 1), size=10)
view.addItem(arm)
view.addItem(receiver)

# === GROUPING ===
telescope_parts = [dish, arm, receiver]
telescope_tip_local = np.array([0, 0, 0.8])

# === Direction Utility ===
def get_direction(azimuth):
    dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
    idx = int(((azimuth + 22.5) % 360) // 45)
    return dirs[idx]

# === MAIN UPDATE LOOP ===
def update_orientation():
    try:
        data, addr = sock.recvfrom(1024)
        decoded = data.decode("utf-8").strip()
        parts = decoded.split(',')
        if len(parts) == 3:
            yaw = float(parts[0])
            pitch = -1 * float(parts[1])
            roll = float(parts[2])

            rot = R.from_euler('zyx', [yaw, pitch, roll], degrees=True)
            mat = rot.as_matrix()
            transform_matrix = np.eye(4)
            transform_matrix[:3, :3] = mat
            transform = pg.Transform3D(*transform_matrix.flatten())

            for part in telescope_parts:
                part.setTransform(transform)

            tip_world = mat @ telescope_tip_local
            trail_points.append(tip_world)
            if len(trail_points) > max_trail_length:
                trail_points.pop(0)

            n = len(trail_points)
            alphas = np.linspace(0.05, 1.0, n)
            colors = np.zeros((n, 4))
            colors[:, 0:3] = 1
            colors[:, 3] = alphas
            trail.setData(pos=np.array(trail_points), color=colors)

            forward_vector = mat @ np.array([0, 0, 1])
            x, y, z = forward_vector
            azimuth = (np.degrees(np.arctan2(-x, y)) + 360) % 360

            #azimuth = (azimuth + 180) % 360  # Flip to match Stellarium

            altitude_angle = np.degrees(np.arcsin(z / np.linalg.norm(forward_vector)))

            # Update compass & HUD
            compass.setAngle(azimuth)
            heading_label.setText(f"Heading: {azimuth:.1f}° {get_direction(azimuth)}")

            altitude_color = "red" if altitude_angle < 15 else "white"
            hud_html = (
                f"<font color='white'>Yaw:    {yaw:.2f}°</font><br>"
                f"<font color='white'>Pitch:  {pitch:.2f}°</font><br>"
                f"<font color='white'>Roll:   {roll:.2f}°</font><br>"
                f"<font color='white'>Azim:   {azimuth:.2f}°</font><br>"
                f"<font color='{altitude_color}'>AltAng: {altitude_angle:.2f}°</font>"
            )
            hud_label.setText(hud_html)

            # === Send to Stellarium ===
            try:
                stellarium_payload = {
                    "az": math.radians(azimuth),
                    "alt": math.radians(altitude_angle),
                    "fov": 60
                }
                requests.post("http://localhost:8090/api/main/view", data=stellarium_payload, timeout=0.2)
            except Exception as e:
                print("Stellarium sync failed:", e)

    except BlockingIOError:
        pass
    except Exception as e:
        print("Error:", e)

# === TIMER LOOP ===
timer = QTimer()
timer.timeout.connect(update_orientation)
timer.start(16)

# === RUN APP ===
if __name__ == '__main__':
    sys.exit(app.exec_())
