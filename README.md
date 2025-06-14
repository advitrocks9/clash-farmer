# Clash of Clans Builder Base Farming Bot

Automates builder base farming in Clash of Clans using computer vision and a hardware mouse controller.

## What it does

Launches Clash of Clans through the Google Play Games emulator on Windows, navigates to the builder base, attacks repeatedly, collects loot, and loops until your resources hit a target threshold. The whole thing runs unattended.

The bot handles the full cycle: detecting game state, starting attacks, deploying troops in phases, using the hero ability, waiting through battle results, collecting rewards, and sailing back to attack again.

## How it works

The pipeline has four main pieces: screen capture, vision, decision logic, and mouse control.

**Screen capture** uses `mss` to grab frames from the emulator window.

**Vision** is split between template matching and OCR. OpenCV template matching finds UI elements (army button, ship, collect buttons, resource icons, error dialogs). EasyOCR reads resource counts (gold, elixir, trophies) from specific screen regions, with 4x upscaling because the text is small and OCR struggles at native resolution. Red error text is detected separately through HSV color masking.

**Decision logic** is a state machine in `main.py` (~370 lines). It figures out where in the game loop you are, what to click next, and when to deploy troops. Attack sequencing deploys the hero first, then troops in phases, fires the ultimate ability, and handles multi-round battles.

**Mouse control** goes through a Makcu device, a custom USB HID controller. This is a physical mouse controller, not software automation, so the game sees real hardware input.

## The Makcu driver

The `makcu/` package (~330 lines) is a custom driver for the Makcu USB HID device. It communicates over serial with baud negotiation (starts at 115200, switches to 4M for speed).

Mouse movement uses a physics simulation with gravity and wind parameters to produce Bezier-like curves. Movements look human because they have acceleration, overshoot, and slight randomness baked in. The driver supports all five mouse buttons (left, right, middle, mouse4, mouse5).

AutoHotkey scripts rebind mouse buttons to keyboard keys for troop selection, since the game maps troop slots to keyboard shortcuts.

## Tech stack

- Python (screen capture, vision, control logic)
- OpenCV (template matching, color detection)
- EasyOCR (reading resource counts from screen)
- mss (fast screen capture)
- pyserial (Makcu device communication)
- pywin32 (window management)
- AutoHotkey (key rebinding)
- Google Play Games emulator (runs the game on Windows)

## Setup

1. Connect the Makcu device via USB.
2. Install the Google Play Games emulator and log into Clash of Clans.
3. Place template images in the `images/` directory (screenshots of UI elements the bot needs to recognize).
4. Set up the AutoHotkey script for troop key rebinds.
5. Install dependencies:

```
pip install -r requirements.txt
```

## Usage

```
python main.py
```

The bot will launch the game, navigate to the builder base, and start farming. It logs resource counts and current state to the console. Kill the process to stop it.

## Dependencies

- numpy
- opencv-python
- easyocr
- mss
- pyserial
- pywin32

## Project structure

```
main.py              # Bot logic, state machine, attack sequencing
rebind.ahk           # AutoHotkey key rebinds for troop selection
images/              # Template images for UI element detection
makcu/               # Hardware mouse controller driver
  connection.py      # Serial transport, baud negotiation
  controller.py      # High-level mouse API, physics-based movement
  mouse.py           # Low-level HID button and movement commands
  enums.py           # MouseButton enum
  errors.py          # Exception classes
```
