# Vimender v1.0

**Vimender** is an advanced modal keyboard navigation, touch gesture, and visual hint controller for Blender 4.0 - 5.2+. Inspired by Vim, Vimium, and modern CAD workflows, it brings mouse-free precision modeling, effortless viewport navigation, and fluid multi-touch gesture control directly to Blender.

---

## 📖 Quick-Start Guide

Vimender transforms Blender's 3D Viewport into an efficient, modal keyboard and gesture-driven workspace inspired by Vim and CAD software. To begin, activate your desired mode using designated shortcuts such as `Ctrl + B` for Navigation, `Ctrl + W` for Tablet, `Ctrl + Alt + P` for Pen, or `Alt + Q` for Walk mode. Once active, move seamlessly with `H, J, K, L`, manipulate active transforms (`G, R, S, E`) with directional nudging, and jump directly to geometry using Vimium-style visual hints (`F` or `C`). Press `Esc` at any time to return safely to Preview mode or press `<` to trigger quick contextual search menus.

---

## 📑 Table of Contents

- [Quick-Start Guide](#-quick-start-guide)
- [Mode Switching & Overview](#-mode-switching--overview)
- [Comprehensive Shortcut Reference](#-comprehensive-shortcut-reference)
  - [1. Orbit & Pan Mode](#1-orbit--pan-mode)
  - [2. Navigation Mode](#2-navigation-mode)
  - [3. Transform Modal Nudges (G, R, S, E)](#3-transform-modal-nudges-g-r-s-e)
  - [4. Visual Jump Hints & Box Select](#4-visual-jump-hints--box-select)
  - [5. 3D Cursor Mode](#5-3d-cursor-mode)
  - [6. Walk / Fly Mode](#6-walk--fly-mode)
  - [7. Tablet & Touch Mode](#7-tablet--touch-mode)
  - [8. Pen Mode](#8-pen-mode)
  - [9. Quick Search Tools](#9-quick-search-tools)
  - [10. Modeling & Boolean Tools](#10-modeling--boolean-tools)
  - [11. Session & File Prompts](#11-session--file-prompts)
- [Preferences & Customization](#-preferences--customization)
- [Installation Guide](#-installation-guide)
- [License](#-license)

---

## 🎛 Mode Switching & Overview

Vimender operates through a non-blocking modal event loop with dedicated modes:

| Mode | Shortcut | Description |
| :--- | :--- | :--- |
| **Orbit / Pan** | `U` (or `P`) | Primary navigation mode for rotating and panning the 3D viewport. |
| **Preview** | `Ctrl + Y` | Clean camera/render preview mode that passes through regular Blender commands. |
| **Navigation** | `Ctrl + B` | Focus traversal for switching between editor areas and workspaces. |
| **Cursor** | `Ctrl + Num5` | Precise 3D cursor placement, axial movements, and geometry snapping. |
| **Walk / Fly** | `Alt + Q` | First-person WASD + HJKL camera traversal with collision and gravity. |
| **Tablet** | `Ctrl + W` | Hardware evdev multi-touch navigation (pinch zoom, pan, orbit, gizmos). |
| **Pen** | `Ctrl + Alt + P` / Edit Menu | Digitizer tablet mode with palm rejection, isolated 2-finger pinch zoom, and strict lock (only `Ctrl+Alt+P` or Edit menu exits). |
| **Mode Search** | `AltGr + <` | Quick popup menu to search and jump to any Vimender or Blender mode. |
| **Exit to Preview** | `Esc` / `Enter` | Safely leaves active navigation/cursor/hint mode back to Preview. |

---

## ⌨️ Comprehensive Shortcut Reference

### 1. Orbit & Pan Mode

Orbit/Pan mode provides fast, directional 3D camera control without needing middle-click mouse dragging.

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `H / J / K / L` | Directional Orbit / Pan | Move left, down, up, right (holding `Shift` accelerates motion). |
| `Arrow Keys` / `Numpad 4/2/8/6` | Directional Orbit / Pan | Alternative arrow / numpad directional movement. |
| `U` (or `P`) | Toggle Orbit / Pan | Switches the active navigation behavior between orbit and pan. |
| `N / M` | Zoom In / Out | Smooth continuous viewport dolly / zoom. |
| `+ / -` / `PageUp / PageDn` | Zoom In / Out | Standard zoom keys. |
| `Ctrl + +` / `Ctrl + -` | Adjust Sensitivity | Scales movement speed (levels from 0.25x up to 3.0x). |
| `1` / `Numpad 1` | Front View | Snap view to Front (`Ctrl + 1`: Back View; `Shift`: Align to Active). |
| `3` / `Numpad 3` | Right View | Snap view to Right (`Ctrl + 3`: Left View; `Shift`: Align to Active). |
| `7` / `Numpad 7` | Top View | Snap view to Top (`Ctrl + 7`: Bottom View; `Shift`: Align to Active). |
| `9` / `Numpad 9` | Opposite View | Orbit view 180° around the current pivot. |
| `0` / `Numpad 0` | Camera View | Look through the active scene camera. |
| `Ctrl + Alt + 0` | Align Camera to View | Repositions active camera to match your current viewpoint. |
| `5` / `Numpad 5` or `Y` / `*` | Persp / Ortho | Toggles between Perspective and Orthographic projection. |
| `/` / `Numpad /` | Local View | Isolates selected objects in local view. |
| `.` (Period) or `I` | Frame Selected | Centers and frames the viewport on selected objects (`view_selected`). |

---

### 2. Navigation Mode

Designed for rapid workspace and editor layout traversal without moving the mouse pointer.

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `H / J / K / L` or `Arrows` | Jump Editor Focus | Shifts keyboard and active focus to the adjacent editor area (Left, Down, Up, Right). |
| `Alt + H` / `Alt + K` | Previous Workspace | Cycles to the previous workspace layout tab. |
| `Alt + L` / `Alt + J` | Next Workspace | Cycles to the next workspace layout tab. |
| `O` | Cycle Areas | Loops focus sequentially across all visible editor windows. |
| `Esc` / `Enter` | Exit Navigation | Returns to Preview mode. |

---

### 3. Transform Modal Nudges (G, R, S, E)

While performing any standard Blender transform operation, Vimender intercepts directional inputs for exact, pixel-perfect keyboard nudging:

| Shortcut | Context | Action |
| :--- | :--- | :--- |
| `G` / `R` / `S` / `E` | Viewport | Start Grab (move), Rotate, Scale, or Extrude. |
| `H / J / K / L` or `Arrows` | During Transform | Smoothly nudge the cursor or active transform along axes. |
| `N / M` | During Transform / Modals | Adjust modal proportional edit size, bevel segment count, array count, solidify thickness, or subsurf levels. |
| `Left Click` / `Enter` | During Transform | Confirm and apply the transform. |
| `Right Click` / `Esc` | During Transform | Cancel the transform and revert to original location. |

---

### 4. Visual Jump Hints & Box Select

Vimium/EasyMotion-inspired visual navigation that overlays 3D geometry with 2-letter labels for instant selection:

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `F` | Hint Click Select | Displays 2-letter jump codes on visible geometry/objects. Type the letters to select. |
| `Shift + F` | Extend Hint Select | Adds the targeted hint element to the current selection. |
| `Alt + F` | Deselect Hint | Removes the targeted hint element from the selection. |
| `C` | Box Hint Select | Activates box selection mode (snap first corner to a hint or position with HJKL). |
| `Shift + C` | Extend Box Select | Extends selection with the defined bounding box. |
| `Esc` / `Right Click` | Cancel Hint Mode | Immediately dismisses hint tags and returns to previous mode. |

---

### 5. 3D Cursor Mode

Provides complete 3D cursor navigation, axial nudges, and geometry snapping without requiring mouse clicks:

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `H / J / K / L` or `Arrows` | Move Cursor | Smooth accelerated cursor movement within the active 2D plane (Hold `Shift` to sprint). |
| `Tab` / `Numpad 5` | Cycle Planes | Switches the active projection plane (`XY` -> `VIEW` -> `XZ` -> `YZ`). |
| `V` / `X` / `Z` / `Y` | Direct Plane Select | Directly select VIEW, XY, XZ, or YZ projection planes. |
| `0` / `O` | Snap to World Origin | Centers the 3D Cursor to coordinate `(0.0, 0.0, 0.0)`. |
| `S` | Snap to Selected | Centers the 3D Cursor to the bounding center of active selection. |
| `C` / `.` (Period) | Center View on Cursor | Frames the 3D viewport directly around the cursor. |
| `+ / -` / `PageUp / PageDn` | Cursor Sensitivity | Adjusts cursor velocity and acceleration. |
| `Esc` / `Enter` / `Space` | Exit Cursor Mode | Closes cursor mode and returns to Preview. |

---

### 6. Walk / Fly Mode

Immersive first-person camera traversal suitable for architectural visualization and level design:

| Shortcut | Action | Description |
| :--- | :--- | :--- |
| `W / S` | Forward / Backward | Moves the camera forwards or backwards along view vector. |
| `A / D` | Strafe Left / Right | Slides the camera laterally. |
| `E / Q` | Fly Up / Down | Elevates or lowers camera height along world Z axis. |
| `H / J / K / L` | Turn / Look | Smooth yaw (look left/right) and pitch (look up/down). |
| `Shift` | Sprint | Doubles forward and strafe travel speeds. |
| `+ / -` | Adjust Sensitivity | Scales walking and looking speed. |
| `Esc` / `Enter` / `Alt + Q` | Exit Walk Mode | Exits first-person controller and locks viewpoint. |

---

### 7. Tablet & Touch Mode

Hardware-accelerated multi-touch navigation designed for 2-in-1 convertibles and touchscreen laptops (Linux evdev):

| Touch Gesture | Action | Description |
| :--- | :--- | :--- |
| **1-Finger Drag** | Viewport Orbit | Natural turntable orbit with smooth flick inertia. |
| **2-Finger Pinch** | Instant Pinch Zoom | Buttery, zero-lag exponential zoom around touch midpoint with zero drift on release. |
| **2-Finger Drag** | Viewport Pan | Sticks the 3D viewport 1:1 to finger movement. |
| **2-Finger Twist** | Viewport Roll | Rotates the viewport around the screen viewing axis. |
| **2-Finger Quick Tap** | Undo | Instant undo (`Ctrl + Z`). |
| **3-Finger Quick Tap** | Redo | Instant redo (`Ctrl + Shift + Z`). |
| **Virtual On-Screen Buttons** | Gizmos & Tools | On-screen hitboxes for `X`, `Y`, `Z` axis locks, `SEL`, `+SHIFT`, `+CTRL`, `+ALT`, `G`, `R`, `S`, and `BOX`. |

---

### 8. Pen Mode

Dedicated stylus tablet mode (`WHITE` indicator) crafted for graphics tablets, Wacom digitizers, and convertible pens:

| Input / Gesture | Behavior | Description |
| :--- | :--- | :--- |
| **Pen / Stylus Tip** | Full Passthrough | 100% native drawing, sculpting, and grease pencil pressure support without modal interception. |
| **Single-Finger Touch** | Palm Rejection | Strictly ignored so resting your hand or palm while sketching never accidentally spins the scene. |
| **2-Finger Hand Pinch** | Isolated View Zoom | Isolated viewport zoom and pan anchored around fingers. |
| **Lower Barrel Button** | Scene Orbit | Hold & drag to rotate the 3D viewport (`view3d.rotate`). |
| **Upper Barrel Button** | Scene Pan | Hold & drag to pan the 3D viewport (`view3d.move`). |
| **C** | Box Hint Select | Triggers box selection without leaving pen workflow. |
| `Ctrl + Alt + P` / Edit Menu | Exit Pen Mode | Strictly locked: only `Ctrl + Alt + P` or the Edit menu toggle exits Pen Mode (ESC and all tool keys pass directly through to Blender). |

---

### 9. Quick Search Tools

Contextual search bars optimized for fast keyboard invocation:

| Shortcut | Search Feature | Description |
| :--- | :--- | :--- |
| `<` (`GRLESS`) | General Search | Opens native Blender operator search dialog centered inside the active 3D region. |
| `Ctrl + <` | Search Objects & Cursor | Quickly locate objects by name or snap cursor directly to any asset in the scene. |
| `AltGr + <` | Mode Search | Filter and switch between all Vimender and Blender editor modes. |
| `Shift + AltGr + <` | Data Search | Fuzzy search all objects, materials, shaders, and textures in the active scene. |

---

### 10. Modeling & Boolean Tools

Integrated mesh operations for rapid hard-surface modeling:

| Shortcut | Mode | Action |
| :--- | :--- | :--- |
| `B` | Edit Mesh | Bevel edges (`mesh.bevel`). |
| `Shift + B` | Edit Mesh | Bevel vertices (`mesh.bevel` with vertex mode). |
| `Ctrl + +` / `Numpad +` | Object Mode | Boolean Brush Union (nondestructive cutter). |
| `Ctrl + Shift + +` | Object Mode | Boolean Auto Union (direct boolean merge). |
| `Ctrl + -` / `Numpad -` | Object Mode | Boolean Brush Difference (cutter subtract). |
| `Ctrl + Shift + -` | Object Mode | Boolean Auto Difference (direct boolean subtract). |
| `Ctrl + *` / `Numpad *` | Object Mode | Boolean Brush Intersect. |
| `Ctrl + Shift + *` | Object Mode | Boolean Auto Intersect. |
| `Ctrl + /` / `Numpad /` | Object Mode | Boolean Brush Slice. |
| `Ctrl + Shift + /` | Object Mode | Boolean Auto Slice. |
| `Ctrl + +` / `Ctrl + -` | Edit Mesh | Selection expand (`select_more`) / Selection shrink (`select_less`). |
| `Ctrl + Shift + +` / `-` | Edit Mesh | Selection next item / Selection previous item. |

---

### 11. Session & File Prompts

Vim-style safe file management preventing accidental data loss:

| Shortcut | Action | Dialog Options |
| :--- | :--- | :--- |
| `Ctrl + Q` | Save & Quit Prompt | Prompts to save current work before closing Blender (`Y`: Save, `N`: Discard, `C` / `Esc`: Cancel). |
| `Ctrl + N` | New File Prompt | Prompts before creating a new scene. |
| `Ctrl + O` | Open File Prompt | Prompts before opening an existing project. |

---

## ⚙️ Preferences & Customization

Navigate to **Edit > Preferences > Add-ons > Vimender**:

1. **GitHub In-Addon Updates**:
   - **Check for Updates**: Queries the GitHub repository for the newest commit or release.
   - **Update Now**: Automatically downloads, extracts, and hot-reloads all Vimender modules without restarting Blender.
2. **Laptop & Layout Options**:
   - **Arrow Keys Layout**: Enables full numpad view switching (1, 3, 7, 0, Local view, Persp/Ortho) using standard top-row numbers and arrow keys on laptops without a physical numeric pad.
3. **Custom Keymap Bindings**:
   - Rebind any trigger key, Ctrl, Shift, or Alt modifier.
   - **Export / Import Keymaps**: Save your key configuration to JSON or restore defaults.

---

## 🚀 Installation Guide

### Option 1: Install from ZIP (Recommended)
1. Download **`Vimender.zip`** from the [GitHub Releases](https://github.com/Auxerov/Vimender) page or generate it from source.
2. In Blender, open **Edit > Preferences > Add-ons**.
3. Click the drop-down menu in the top-right corner and choose **Install from Disk...**.
4. Select `Vimender.zip` and enable **Interface: Vimender**.

### Option 2: Direct Symlink (For Developers)
Clone this repository and symlink it directly into your Blender addon directory:

```bash
git clone https://github.com/Auxerov/Vimender.git ~/Documents/Projects/Vimender
ln -s ~/Documents/Projects/Vimender ~/.config/blender/5.2/scripts/addons/Vimender
```

---

## 📜 License

Distributed under the GNU General Public License v3.0 (GPL-3.0). See [LICENSE](LICENSE) for details.
