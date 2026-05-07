# AFC-ACE Integration

<div align="center">

**Native ACE Pro Support for AFC Multi-Material System**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Status: Beta](https://img.shields.io/badge/Status-Beta-orange)](https://github.com/mon5termatt/AFC-ACE-Integration)
[![Klipper](https://img.shields.io/badge/Klipper-Compatible-blue)](https://www.klipper3d.org/)

</div>

---

## 📖 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Quick Start](#quick-start)
- [Documentation](#documentation)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Installation](#installation)
- [Usage Examples](#usage-examples)
- [Contributing](#contributing)
- [Credits](#credits)
- [License](#license)

## Overview

AFC-ACE Integration brings native Anycubic Color Engine (ACE) Pro support to the Armored Turtle AFC (Automated Filament Control) multi-material system. This standalone integration combines AFC's powerful macro ecosystem with ACE Pro's USB-based hardware, enabling seamless multi-color/multi-material printing.

### What It Does

- **Bridges AFC ↔ ACE Pro** - Makes ACE Pro work as a native AFC unit (like BoxTurtle/NightOwl)
- **Auto-Detection** - Automatically discovers ACE devices on USB
- **Multi-ACE Support** - Scale to 8, 12, 16+ lanes with multiple ACE devices
- **No Dependencies** - Standalone integration, no KlipperACE required
- **Full AFC Features** - Endless spool, tip forming, purging, spoolman, etc.

## Features

### ✨ Core Features

- **🔌 Plug-and-Play** - Auto-detects ACE devices, generates config automatically
- **📡 USB Protocol** - Native ACE Pro protocol implementation (JSON-RPC over serial)
- **🎯 Lane Management** - Each ACE slot becomes an AFC lane (4 lanes per ACE)
- **🔄 Multi-ACE** - Support for 2, 3, 4+ ACE devices (8, 12, 16+ lanes)
- **📍 Stable Mapping** - Uses `/dev/serial/by-path` for reliable device identification
- **🌡️ Dryer Control** - Start/stop ACE dryer via G-code commands
- **⚡ Feed Assist** - ACE's built-in feed assist for reliable feeding

### 🎨 AFC Integration

All standard AFC features work seamlessly:

| Feature | Status | Description |
|---------|--------|-------------|
| Tool Changes | ✅ | T0, T1, T2, T3... commands work natively |
| Endless Spool | ✅ | Auto-switch to backup lane on runout |
| Tip Forming | ✅ | Clean filament tips for reliable unloads |
| Purge Macros | ✅ | POOP, BRUSH, KICK - full macro support |
| Gate Mapping | ✅ | Material type, color, temperature per lane |
| Spoolman | ✅ | Filament tracking and management |
| LED Feedback | ✅ | Visual status indicators |
| Calibration | ✅ | Lane distance tuning |

## Quick Start

### Installation (3 minutes)

```bash
# 1. Install AFC (if not already installed)
cd ~/
git clone https://github.com/ArmoredTurtle/AFC-Klipper-Add-On.git
cd AFC-Klipper-Add-On
./install-afc.sh
```

**`install-afc.sh` — bootstrap only**

Running `install-afc.sh` mainly installs the AFC Klipper extras and drops starter files under `~/printer_data/config/AFC/`. The menu choices below only control **what that script writes for a BoxTurtle-shaped install**.

**After you run `utilities/detect_ace_devices.py --generate-config` and save the output as `AFC_ACE_Pro.cfg`, none of that BoxTurtle-specific menu detail is what defines your system anymore** — your real unit and lanes come from the generated `[AFC_ACE …]` and `[AFC_lane …]` sections. You must **remove** the Turtle unit file(s) the installer created (see step 3 below), or Klipper will still load duplicate/wrong lane definitions via `[include AFC/*.cfg]`.

If you are doing a **pure ACE** setup (no BoxTurtle hardware), the interactive choices are largely irrelevant beyond getting a minimal AFC tree; you still need to **delete** the Turtle artifacts after generating ACE config.

---

**Typical menu picks when the installer was run as “BoxTurtle (4-lane), no buffer” (only affects files until you replace them with ACE config)**

The installer menu is interactive. Set at least the following before choosing **I** (install):

| Key | Setting | What to choose |
|-----|---------|----------------|
| **T** | Installation type | **BoxTurtle (4-Lane)** (this is the default; change only if you use 8-lane or another unit) |
| **9** | Toolhead sensor vs ramming | **Sensor** — uses a filament switch at the extruder. Do **not** use *Ramming* unless you are using a TurtleNeck buffer for ram-based detection. |
| **A** | Toolhead sensor pin | Your real Klipper pin for the **filament presence sensor at the toolhead** (the switch that sees filament in the extruder). Examples: `^!PB3`, `toolhead:gpio6`, or a board alias like `nhk:gpio13` — use the same naming style as the rest of your `printer.cfg`. You must set this; leaving it **Unknown** will not configure the pin. |
| **B** | Buffer type | **None** — cycle with **B** until it shows `None` (defaults are often `TurtleNeck` → `TurtleNeckV2` → `None`). ACE feeds straight to the extruder; you do not need a BoxTurtle buffer section for basic ACE operation. |
| **C** | BoxTurtle name | Optional: default **Turtle_1** is fine unless you rename the unit in config. |
| **I** | Install | Writes `~/printer_data/config/AFC/`, links Klipper extras, and can add `[include AFC/*.cfg]` to `printer.cfg` when “Add AFC includes?” is enabled. |

After install, you would normally finish BoxTurtle MCU serial/CAN in `~/printer_data/config/AFC/AFC_<name>.cfg` — **for ACE-only, skip that and remove that file after step 3 instead.**

```bash
# 2. Install AFC-ACE
cd ~/
git clone https://github.com/mon5termatt/AFC-ACE-Integration.git
cd AFC-ACE-Integration
./install-afc-ace.sh

# 3. Generate config
python3 utilities/detect_ace_devices.py --generate-config > ~/printer_data/config/AFC/AFC_ACE_Pro.cfg
```

**Remove Turtle / BoxTurtle config files (required for ACE-only)**

`[include AFC/*.cfg]` loads every `.cfg` in that folder. The installer copies a **BoxTurtle unit** file such as:

- `~/printer_data/config/AFC/AFC_Turtle_1.cfg` (default name), or `AFC_<BoxTurtle_name>.cfg` if you changed it in the installer.

**Delete that file** (or move it outside `AFC/`) so only ACE lanes from `AFC_ACE_Pro.cfg` remain. Otherwise you keep stale `[AFC_BoxTurtle …]` / lane sections that conflict with `[AFC_ACE …]`.

If you **do not** have a BoxTurtle AFC board, also remove the unused MCU snippet and includes, for example:

- `~/printer_data/config/AFC/mcu/AFC_Lite.cfg` (4-lane install) or `mcu/AFC_Pro.cfg` (8-lane), and any `[include mcu/…]` lines in `AFC_Hardware.cfg` that reference them.

Keep `AFC.cfg` and the parts of `AFC_Hardware.cfg` you still need (e.g. toolhead filament sensor) and edit `AFC_ACE_Pro.cfg` for extruder name, pins, etc., as documented in this repo.

```bash
# 4. Restart Klipper
sudo systemctl restart klipper
```

> Note: `install-afc-ace.sh` ensures `[include AFC/*.cfg]` is present in `printer.cfg` **above** Klipper’s `SAVE_CONFIG` auto-generated block (and avoids duplicates), so you generally do not need to manually `echo` it in.

### First Use

```gcode
PREP    # Initialize lanes
T0      # Load filament from lane 1
T1      # Switch to lane 2
```

## Documentation

| Document | Description |
|----------|-------------|
| **[INSTALLATION.md](./INSTALLATION.md)** | Complete installation guide with troubleshooting |
| **[USAGE.md](./USAGE.md)** | Operating manual with examples and workflows |
| **[TESTING_GUIDE.md](./TESTING_GUIDE.md)** | Testing procedures and validation steps |
| **[PROJECT_SUMMARY.md](./PROJECT_SUMMARY.md)** | Technical architecture and code overview |

## Project Structure

```
AFC-ACE-Integration/
├── extras/                          # Python modules
│   ├── AFC_ACE.py                  # ACE unit driver (implements AFC interface)
│   ├── AFC_ACE_protocol.py         # USB protocol handler (JSON-RPC)
│   └── AFC_ACE_discovery.py        # Auto-detection and enumeration
│
├── config/                          # Configuration templates
│   ├── mcu/
│   │   └── ACE_Pro.cfg             # Comprehensive template with docs
│   ├── AFC_ACE_single_example.cfg  # Single ACE (4 lanes) example
│   └── AFC_ACE_multi_example.cfg   # Multi-ACE (8+ lanes) example
│
├── utilities/
│   └── detect_ace_devices.py       # Auto-detection and config generator
│
├── install-afc-ace.sh               # Installation script
│
├── README.md                        # Overview and quick start
├── INSTALLATION.md                  # Install guide
├── USAGE.md                         # User manual
├── TESTING_GUIDE.md                 # Test procedures
└── PROJECT_SUMMARY.md               # Technical details
```

## Requirements

### Hardware
- **ACE Pro** - One or more Anycubic Color Engine Pro devices
- **USB Cable** - Data-capable cable (not charge-only)
- **3D Printer** - Running Klipper firmware
- **Linux Host** - Raspberry Pi, Orange Pi, or similar (for `/dev/serial/by-path` support)

### Software
- **Klipper** - 3D printer firmware
- **Moonraker** - Klipper API server
- **AFC-Klipper-Add-On** - Multi-material system
- **Python 3** - With pyserial library

## Installation

See [INSTALLATION.md](./INSTALLATION.md) for complete installation guide.

### Quick Install

```bash
cd ~/AFC-ACE-Integration
./install-afc-ace.sh
```

The installer:
1. ✅ Checks dependencies (Klipper, AFC)
2. ✅ Installs Python libraries
3. ✅ Creates symlinks to Klipper
4. ✅ Copies configuration templates
5. ✅ Auto-detects ACE devices

## Usage Examples

### Single ACE (4 lanes)

```ini
[AFC_ACE ace1]
auto_detect: true
extruder: extruder

[AFC_lane lane1]
unit: ace1:0
map: T0
extruder: extruder

# ... lanes 2-4
```

### Multi-ACE (8 lanes)

```ini
# First ACE
[AFC_ACE ace1]
auto_detect: true
device_index: 0

# Second ACE
[AFC_ACE ace2]
auto_detect: true
device_index: 1

# Lanes T0-T7
```

### Commands

```gcode
# Initialization
PREP                                    # Initialize all lanes

# Tool changes
T0                                      # Select lane 1
T4                                      # Select lane 5 (multi-ACE)

# Dryer control
ACE_START_DRYING TEMP=50 DURATION=240  # Start dryer
ACE_STOP_DRYING                         # Stop dryer

# Status
ACE_GET_STATUS                          # Show device status

# Manual control
ACE_FEED INDEX=0 LENGTH=50 SPEED=50    # Feed 50mm
ACE_RETRACT INDEX=0 LENGTH=50 SPEED=50 # Retract 50mm

# Feed assist
ACE_ENABLE_FEED_ASSIST LANE=0          # Enable feed assist for a slot
ACE_DISABLE_FEED_ASSIST LANE=0         # Disable feed assist for a slot

# Endless spool
AFC_ENDLESS_SPOOL ENABLE=1              # Enable auto-switching

# Gate mapping
ACE_GATE_MAP GATE=0 TYPE=PLA COLOR=FF0000 TEMP=210

# RFID (untested)
ACE_ENABLE_RFID                         # Enable RFID reader
ACE_GET_FILAMENT_INFO INDEX=0           # Query RFID filament info for slot 0 (repeat 0-3)
ACE_DISABLE_RFID                        # Disable RFID reader
```

## Contributing

Contributions welcome! Please:

1. **Fork** the repository
2. **Create** a feature branch
3. **Test** your changes thoroughly
4. **Document** new features
5. **Submit** a pull request

### Development Setup

```bash
git clone https://github.com/mon5termatt/AFC-ACE-Integration.git
cd AFC-ACE-Integration

# Make changes
# Test on real hardware
# Document in appropriate .md files
```

### Reporting Issues

When reporting bugs, please include:
- Hardware setup (number of ACEs, USB connection)
- Software versions (Klipper, AFC)
- Error logs (`~/printer_data/logs/klippy.log`)
- Configuration files
- Steps to reproduce

## Credits

This project builds upon excellent work by:

### Projects
- **[AFC-Klipper-Add-On](https://github.com/ArmoredTurtle/AFC-Klipper-Add-On)** by Armored Turtle
  - Multi-material management framework
  - Macro system and lane management

- **[BunnyACE](https://github.com/BlackFrogKok/BunnyACE)** by BlackFrogKok
  - ACE Pro protocol implementation
  - USB auto-detection system

- **[ACEPRO](https://github.com/Kobra-S1/ACEPRO)** by Kobra-S1
  - Reference Klipper integration and ACE Pro behaviors that informed this project

### Special Thanks
- **Armored Turtle Community** - AFC development and support
- **Klipper Team** - Firmware foundation
- **ACE Pro Users** - Testing and feedback

## License

This project is licensed under the MIT License - see [LICENSE](./LICENSE) file for details.

### Code Attribution

This project incorporates code adapted from:
- **BunnyACE** - Protocol and discovery code
- **AFC-Klipper-Add-On** - Interface implementation
- **ACEPRO** - Klipper integration and ACE Pro behaviors

All adapted code is clearly attributed and compatible with MIT licensing.

---

<div align="center">

**[Documentation](./INSTALLATION.md)** • **[Issues](https://github.com/mon5termatt/AFC-ACE-Integration/issues)** • **[Discord](https://discord.gg/eT8zc3bvPR)**

Made with ❤️ for the AFC community

</div>
