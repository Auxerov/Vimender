# Vimender Architectural & Developer Guide

> **Target Audience**: AI Coding Agents, Core Contributors, and Blender Addon Developers.  
> **Repository**: [https://github.com/Auxerov/Vimender.git](https://github.com/Auxerov/Vimender.git)  
> **Blender Compatibility**: Blender 4.0 through Blender 5.2+ (including Steam and native Linux / Wayland builds).  
> **Language Standard**: All code comments, docstrings, UI labels, and operator reports must be strictly in **English**.

---

## 1. System Overview & Core Philosophy

**Vimender** transforms Blender's 3D Viewport into a modal, keyboard-centric and gesture-driven workspace inspired by Vim, Vimium, and modern CAD workflows.

### 1.1 Modal Paradigms
Instead of navigating Blender's deep nested hotkey menus or relying heavily on a 3-button mouse and numpad, Vimender introduces distinct runtime modes managed by a single non-blocking modal event loop:

- **Navigation Mode (`NAV`)**: Fast viewport orbit, pan, zoom, view snapping (Top, Front, Right, Camera, Ortho/Persp) using `H, J, K, L` and single-key commands.
- **Cursor Mode (`CURSOR`)**: 3D cursor placement, nudging along axes, and snapping to geometry (vertices, edges, faces, origins).
- **Walk / Fly Mode (`WALK`)**: WASD / HJKL first-person viewport traversal with gravity and collision options.
- **Tablet / Touch Mode (`TABLET`)**: Multi-touch gesture navigation (pinch-to-zoom, two-finger pan/orbit), virtual on-screen modifier buttons (`SEL`, `+SHIFT`, `+CTRL`, `+ALT`), axis constraints (`X`, `Y`, `Z`), and touch transform gizmos.
- **Hint Mode (`HINT`)**: Vimium/EasyMotion-style two-character visual jump tags projected directly onto 3D objects, vertices, edges, or faces in the viewport.
- **Preview Mode (`PREVIEW`)**: Fast render and camera review mode.
- **Pen Mode (`PEN`)**: Dedicated white-themed mode (`WHITE = (0.98, 0.98, 0.98, 1.0)`) for graphics tablets, digitizer styluses, and 2-in-1 convertible laptops. Default shortcut: `Ctrl+Alt+P` (guaranteed collision-free). Native mouse navigation (MMB orbit/pan, scroll zoom) and pen drawing/sculpting pass through directly to Blender without disruption. Two-finger hand pinching is isolated exclusively for viewport zoom and anchor panning, while single-finger touches and palm contacts are ignored.

---

## 2. Directory & Module Responsibilities

| File | Primary Responsibility |
| :--- | :--- |
| [`__init__.py`](file:///home/auxerov/Documents/Projects/Vimender/__init__.py) | Addon registration lifecycle, auto-start timers, topbar window menu integration, class registration. |
| [`state.py`](file:///home/auxerov/Documents/Projects/Vimender/state.py) | Global state singleton `S` (`VimState`), JSON persistence (`vimender_config.json`), math helper vectors/quaternions. |
| [`preferences.py`](file:///home/auxerov/Documents/Projects/Vimender/preferences.py) | User preferences UI, custom keymap bindings, export/import JSON, and the GitHub updater. |
| [`operators.py`](file:///home/auxerov/Documents/Projects/Vimender/operators.py) | Core modal operator (`WM_OT_vim_mode_toggle`), transform nudge hooks (`G, R, S, E`), and search operators. |
| [`ui.py`](file:///home/auxerov/Documents/Projects/Vimender/ui.py) | GPU drawing pipeline (hints, mode badges, tablet buttons, axis indicators) and pie menu patching. |
| [`mode_nav.py`](file:///home/auxerov/Documents/Projects/Vimender/mode_nav.py) | Viewport camera manipulation (orbit, pan, zoom, view align, dolly). |
| [`mode_cursor.py`](file:///home/auxerov/Documents/Projects/Vimender/mode_cursor.py) | 3D cursor navigation, axial nudges, and geometry snapping. |
| [`mode_walk.py`](file:///home/auxerov/Documents/Projects/Vimender/mode_walk.py) | First-person camera walk/fly controller. |
| [`mode_tablet.py`](file:///home/auxerov/Documents/Projects/Vimender/mode_tablet.py) | Touch gesture recognition, stylus/finger tracking, virtual hitboxes, and touch selection. |
| [`mode_pen.py`](file:///home/auxerov/Documents/Projects/Vimender/mode_pen.py) | Pen Mode controller: pass-through mouse/stylus events, isolated 2-finger multi-touch pinch-zoom navigator (`_PenNavigator`). |
| [`mode_hint.py`](file:///home/auxerov/Documents/Projects/Vimender/mode_hint.py) | 3D-to-2D screen coordinate projection, visual hint code generation, and jump tree matching. |

---

## 3. Core Architectural Rules for AI Agents

When modifying or extending Vimender, you **MUST** follow these rules:

### 3.1 Global State Management (`state.py`)
- All runtime flags, active modes, modal states, and temporary caches must be stored in the global singleton `S` (`VimState`).
- **Never** create independent state dictionaries or module-level mutable variables that can get out of sync.
- Access preferences via `get_preferences(context)` dynamically:
  ```python
  from .state import get_preferences
  prefs = get_preferences(context)
  ```
  *Note:* Do not hardcode `context.preferences.addons['Vimender']` because the addon directory name may be `Vimender`, `Vimender-main`, or a symlink.

### 3.2 Modal Key Interception & Passthrough
The main modal operator is `WM_OT_vim_mode_toggle` in [`operators.py`](file:///home/auxerov/Documents/Projects/Vimender/operators.py).
- **Swallowed Events**: If Vimender handles an action (e.g. `H, J, K, L` navigation), the handler **must** return `{'RUNNING_MODAL'}` to prevent Blender from executing default hotkeys.
- **Passed Through Events**: If an event belongs to Blender (e.g. file dialogs, system menus, text inputs), return `{'PASS_THROUGH'}`.
- **Transform Hook Mechanics (`G, R, S, E`)**:
  - When the user presses `G` (grab/translate), `R` (rotate), `S` (scale), or `E` (extrude):
    1. Vimender intercepts the keypress.
    2. Marks `S.transform_active = True`.
    3. Records initial mouse coordinates.
    4. Returns `{'PASS_THROUGH'}` so Blender initiates its native modal transform operator.
  - While `S.transform_active` is True:
    - `H, J, K, L` (and arrow keys if `arrow_keys_layout` is enabled) are intercepted to nudge the cursor position or step the transform.
    - When `LEFTMOUSE`, `RET`, `ESC`, or `RIGHTMOUSE` is pressed, the transform concludes, `S.transform_active` resets to False, and Vimender resumes normal navigation.
  - **Do NOT** block `LEFTMOUSE` or `RET` during transform passes, or Blender's native transform will become unresponsive.

### 3.3 3D Viewport Region Clamping
Whenever calculating mouse positions or warping the cursor via `context.window.cursor_warp(x, y)`:
- Always clamp coordinates to the active 3D region:
  ```python
  rx = max(region.x + 2, min(target_x, region.x + region.width - 2))
  ry = max(region.y + 2, min(target_y, region.y + region.height - 2))
  ```
- Warping outside the active region causes event loss or erratic viewport jumps on Linux Wayland and multi-monitor setups.

### 3.4 GPU Drawing Architecture (`ui.py`)
- Drawing is done via `bpy.types.SpaceView3D.draw_handler_add` using `POST_PIXEL`.
- **GPU Shaders**: Blender 4.x and 5.x use `gpu.shader.from_builtin('UNIFORM_COLOR')` (or `2D_UNIFORM_COLOR` fallback).
- **Batch Submission**: Group vertices into batches using `gpu.batch.batch_for_shader()` rather than drawing line-by-line.
- **UI Scale**: Always multiply pixel offsets by `context.preferences.system.ui_scale` so elements scale correctly on 4K/HiDPI screens.

---

## 4. Touch & Gesture Pipeline (`mode_tablet.py`)

Tablet mode supports touchscreens, styluses, and 2-in-1 convertible laptops.

### 4.1 Gesture State Machine
```
Idle Touch (1 finger) ---> Motion > Threshold ---> Viewport Orbit
Two-Finger Touch     ---> Distance Change   ---> Pinch-to-Zoom
                     ---> Parallel Delta    ---> Two-Finger Pan
Three-Finger Tap     ---> Trigger Undo / Pie Menu
```

### 4.2 Virtual Screen Hitboxes
To enable tablet-only workflows without a physical keyboard:
- **Bottom-Left Toolbar**: Virtual selection mode buttons:
  - `SEL` (Normal Select)
  - `+SHIFT` (Multi-Select toggle)
  - `+CTRL` (Deselect toggle)
  - `+ALT` (Loop & Depth Select toggle)
- **Bottom-Right Toolbar**: Axial constraint buttons (`X`, `Y`, `Z`).
- Hitboxes are calculated in 2D region coordinates using `get_tablet_selection_buttons(region, ui)` and checked on `LEFTMOUSE` / Touch Press events before passing events to the 3D scene.

---

## 5. GitHub Updater (`preferences.py`)

The addon includes a self-updating system pulling directly from [https://github.com/Auxerov/Vimender.git](https://github.com/Auxerov/Vimender.git).

### 5.1 Dual-Strategy Update Mechanism
1. **Git Repository Mode**:
   - If the addon directory contains a `.git` folder (or is a symlink to a cloned git directory):
   - Check: `git ls-remote https://github.com/Auxerov/Vimender.git HEAD`
   - Update: `git fetch origin main && git reset --hard origin/main`
2. **ZIP Archive Fallback**:
   - If `.git` is not present (standard zip install via Blender's addon manager):
   - Check: GitHub REST API `https://api.github.com/repos/Auxerov/Vimender/commits/main`
   - Update: Downloads `https://github.com/Auxerov/Vimender/archive/refs/heads/main.zip` using standard Python `urllib.request`.
   - Extracts archive in a temporary directory and overwrites addon files in-place using `shutil.copy2` and `shutil.copytree`.

### 5.2 Hot Module Reloading
After pulling new code, the updater reloads all submodules in `sys.modules`:
```python
submodules = [
    "state", "preferences", "mode_walk", "mode_tablet",
    "mode_cursor", "mode_nav", "mode_preview", "mode_pen", "mode_hint", "ui", "operators"
]
```
This applies bug fixes and new features immediately without requiring a full Blender restart.

---

## 6. Recent Fixes & Change Log

- **Pen Mode (`PEN`) Implementation**:
  - Cloned and adapted from Preview Mode with a clean white visual theme (`WHITE = (0.98, 0.98, 0.98, 1.0)`).
  - Assigned collision-free shortcut `Ctrl+Alt+P` (configurable via Preferences under Mode Switching Shortcuts).
  - Seamless pass-through for standard mouse navigation and stylus/pen drawing without interruption.
  - Dedicated multi-touch gesture processing (`_PenNavigator`) that isolates two-finger hand pinching (`ABS_MT`) exclusively for viewport zoom and anchor panning, discarding single-finger touches and palm contacts to prevent accidental scene rotation while drawing.
- **Transform & Nudge Fix (`G, R, S, E` + `H, J, K, L`)**:
  - Restored modal nudge functionality when executing Grab, Rotate, Scale, or Extrude.
  - Intercepts directional keys and dispatches smooth delta movements while Blender native transform is active.
- **Bounding Box Tool (`C`)**:
  - Fixed Box Hint Selection in 3D Viewport to reliably compute screen-projected bounding boxes.
- **Laptop / No-Numpad Support**:
  - Added `arrow_keys_layout` preference to seamlessly route arrow keys to navigation and view snapping without requiring a dedicated numpad.
- **Linux Wayland & Steam Blender Parity**:
  - Unified configurations across Steam Blender (`~/.local/share/Steam/steamapps/common/Blender/blender`) and system Blender installations.
- **Complete English Localization**:
  - All UI strings, comments, and operator messages translated to standard English.
