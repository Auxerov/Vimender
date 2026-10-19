# Vimender

**Vimender** is a modal keyboard navigation, touch gesture, and visual hint controller for Blender 4.0 - 5.2+. Inspired by Vim and Vimium, it enables fast, fluid, and mouse-free 3D modeling and navigation.

---

## ✨ Features

- **Modal Navigation (`Ctrl + B`)**: Navigate the 3D viewport using `H, J, K, L` for orbit, pan, and zoom, plus quick view snapping (Top, Front, Right, Camera, Ortho/Persp).
- **Transform Nudges (`G, R, S, E` + `H, J, K, L`)**: Smoothly nudge objects, vertices, edges, or faces with directional keys during active Grab, Rotate, Scale, or Extrude operations.
- **Visual Jump Hints (`F` / `C`)**: Vimium/EasyMotion-style two-letter jump tags in 3D space to instantly focus or select elements.
- **Laptop & Arrow Keys Layout**: Full support for keyboards without a physical numpad via the preferences toggle.
- **Tablet & Touch Mode (`Ctrl + W`)**: Multi-touch gestures (pinch-to-zoom, two-finger pan/orbit), virtual selection toolbars, and touch transform gizmos.
- **3D Cursor Navigation (`Ctrl + Num5`)**: Precise cursor positioning, axis nudges, and geometry snapping.
- **First-Person Walk / Fly (`Alt + Q`)**: Immersive WASD / HJKL camera movement with gravity and collision options.
- **GitHub Updates**: Check and update directly from GitHub inside Blender Preferences.

---

## 🚀 Installation

1. Download the latest `Vimender.zip` from [GitHub Releases](https://github.com/Auxerov/Vimender) or clone this repository:
   ```bash
   git clone https://github.com/Auxerov/Vimender.git
   ```
2. In Blender, navigate to **Edit > Preferences > Add-ons**.
3. Click the down arrow in the top right and select **Install from Disk...**.
4. Choose the downloaded ZIP file or symlink the folder into your Blender addons directory:
   ```bash
   ln -s /path/to/Vimender ~/.config/blender/5.2/scripts/addons/Vimender
   ```
5. Enable **Interface: Vimender**.

---

## ⚙️ Configuration & Updates

Open **Edit > Preferences > Add-ons > Vimender**:
- **Addon Updates (GitHub)**:
  - Click **Check for Updates** to query GitHub for new releases.
  - Click **Update Now** to pull the latest version and reload modules immediately without restarting Blender.
- **Input Devices & Layout**:
  - Toggle **Arrow Keys Layout** for laptops without a numpad.
- **Keymaps**:
  - Customize any shortcut or export/import your configuration as JSON.

---

## 🛠️ Architecture & Contributing

For developers and AI coding agents working on Vimender, read [ARCHITECTURE.md](ARCHITECTURE.md) for detailed guidelines on the modal event loop, transform nudge mechanics, touch gesture pipelines, and multi-version compatibility.

---

## 📜 License

Distributed under the GPL-3.0 License. See [LICENSE](LICENSE) for more information.
