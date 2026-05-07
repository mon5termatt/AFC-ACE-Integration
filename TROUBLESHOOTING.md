# AFC-ACE Troubleshooting Guide

## "AFC_hub not found" Error

### Error Message
```
Error: AFC_hub not found in configuration
please make sure there is a [AFC_hub <hub_name>] defined in your configuration
```

### Cause
This error occurs when your config references a hub but doesn't define it. ACE Pro **does not need a hub** for normal operation.

### Quick Fix

**Run the config checker:**
```bash
cd ~/AFC-ACE-Integration
python3 utilities/check_config.py ~/printer_data/config
```

This will show you exactly which file and line has the hub reference.

### Manual Fix

**Step 1: Find the hub reference**
```bash
grep -n "^[^#]*hub:" ~/printer_data/config/AFC/*.cfg
```

**Step 2: Comment it out**

Edit the file and change:
```ini
hub: hub
```

To:
```ini
# hub: hub
```

**Step 3: Restart Klipper**
```bash
sudo systemctl restart klipper
```

### Correct Config for ACE (No Hub)

```ini
[AFC_ACE ace1]
auto_detect: true
extruder: extruder
# NO hub line here!

[AFC_lane lane1]
unit: ace1:0
map: T0
extruder: extruder
# NO hub line here either!
```

### When You Actually Need a Hub

If you have a **physical AFC hub** installed between your ACE and extruder, you need to define it:

```ini
# Define the hub first
[AFC_hub hub]
# Your hub configuration
# (see AFC documentation for hub config)

# Then reference it in your unit
[AFC_ACE ace1]
auto_detect: true
extruder: extruder
hub: hub    # ← Now this works because hub is defined above
```

### Use Minimal Config

If you're still having issues, start with the minimal config:

```bash
cp ~/AFC-ACE-Integration/config/AFC_ACE_minimal.cfg ~/printer_data/config/AFC/AFC_ACE_Pro.cfg
```

This is guaranteed to work without hub errors.

## Unknown command: `T0`, `T1`, …

AFC registers `T0`–`Tn` as real G-code commands during **PREP** (`TcmdAssign`), same as Box Turtle. If Klipper says **Unknown command: "T0"**:

1. Run **`PREP`** once after a clean start (the ACE driver must call `TcmdAssign` from `system_Test`; older `AFC_ACE.py` builds skipped this).
2. Restart Klipper after updating the ACE integration, then run **`PREP`** again.
3. Remove or rename any **`[gcode_macro T0]`** (etc.) in `printer.cfg` that would conflict with AFC’s registration. If a `T0` handler already exists, AFC logs a mapping error and `T0` stays unregistered.

### ACE motor does not turn during load / retract

Usually **not** “feed assist must be on.” The ACE Pro USB API expects **`feed_filament`** (params `length`, not `len`) and **`unwind_filament`** for retracts, plus **`start_feed_assist` / `stop_feed_assist`**. An older integration that called `feed` / `back` / `feed_assist` can get responses that never spin the motor. Current `AFC_ACE_protocol.py` matches **ACEPRO `PROTOCOL.md`**. Feed assist is turned **off** before each feed (same pattern as ACEPRO), then turned on again after a successful long feed if AFC asked for assist.

### ACE does not pull much filament on `T0` / `TOOL_LOAD`

AFC’s direct load uses **`dist_hub`** for the first feed (default often **~60 mm**). ACE Pro needs a **long** feed to the extruder (often **2500–3500 mm**). The driver increases any shorter **`hub: direct`** request to **`[AFC_ACE …] direct_tool_load_mm`** (default **2800**). Set **`direct_tool_load_mm`** and/or **`dist_hub`** on each lane to match your machine. If feeds still do nothing, check **`klippy.log`** for **ACE feed failed** (USB/protocol errors now raise instead of failing silently).

### Internal error on `CHANGE_TOOL`: `'function' object has no attribute 'afc_bowden_length'`

AFC’s `TOOL_LOAD` reads **`cur_hub.afc_bowden_length`** in some paths (e.g. `homing_enabled` + `home_to_tool` + toolhead sensor). For **`hub: direct`**, stock AFC sets `hub_obj` to a **lambda** with only a few fields — it does not set bowden lengths unless the ACE driver patches it.

1. Use current **`AFC_ACE.py`**: it copies **`afc_bowden_length`** / **`afc_unload_bowden_length`** from **`[AFC_ACE …]`** onto that lambda after connect and on every **`select_lane`**.
2. Set the same options you use on a Turtle **`[AFC_hub …]`** (mm along the filament path used for homing math), for example:

```ini
[AFC_ACE ace1]
afc_bowden_length: 1200
afc_unload_bowden_length: 1200
```

If **`dist_hub`** on a lane is **larger**, that value wins for the lambda’s **`afc_bowden_length`**. Restart Klipper after editing.

---

## "No ACE devices found" Error

### Symptoms
- `detect_ace_devices.py` finds no devices
- Auto-detection fails

### Checklist

**1. Physical Connection**
```bash
# Check USB enumeration
lsusb | grep -i ace
lsusb | grep GD  # ACE uses GDMicroelectronics

# Check tty devices
ls -l /dev/ttyACM*
```

**2. Power**
- ✅ ACE Pro is powered on (check lights)
- ✅ USB cable is connected

**3. Cable Quality**
- ✅ Use data-capable USB cable (not charge-only)
- ✅ Try a different cable if unsure

**4. Permissions**
```bash
# Check current user groups
groups

# Add user to serial groups if needed
sudo usermod -a -G dialout $USER
sudo usermod -a -G tty $USER

# Logout and login for group changes to take effect
```

**5. Test Manual Connection**
```bash
# Try to read from device
cat /dev/ttyACM0
# Press Ctrl+C after a second

# If "Permission denied", check groups above
```

---

## "Config object 'AFC' not found" Error

### Cause
AFC base system not loaded before AFC-ACE

### Fix

Ensure correct include order in `printer.cfg`:

```ini
# 1. Include AFC base first
[include AFC/AFC.cfg]

# 2. Then include all AFC configs (includes AFC_ACE_Pro.cfg)
[include AFC/*.cfg]
```

Or simpler:
```ini
# This loads everything in correct order
[include AFC/*.cfg]
```

---

## Tool Change Failures

### Symptoms
- Filament doesn't feed
- Load times out
- Filament stops mid-feed

### Solutions

**1. Enable Feed Assist**
```gcode
ACE_ENABLE_FEED_ASSIST LANE=0
```

**2. Check Filament Tip**
- Cut at 45° angle
- Remove blobs/strings
- Fresh, clean cut

**3. Adjust Speed**
```gcode
# Try slower feed
ACE_FEED INDEX=0 LENGTH=50 SPEED=30

# Or faster
ACE_FEED INDEX=0 LENGTH=50 SPEED=60
```

**4. Calibrate Distance**
```gcode
LANE_CALIBRATION
```

**5. Check ACE Status**
```gcode
ACE_GET_STATUS
```

Look for errors in slot status.

---

## USB Connection Drops

### Symptoms
- Device disconnects randomly
- Communication timeouts
- "Device not found" mid-print

### Solutions

**1. Check USB Power**
```bash
# Check kernel messages
dmesg | tail -50 | grep -i usb
```

Look for "device disconnect" or "over-current" messages.

**2. Use Powered USB Hub**
- ACE Pro may draw more power than Pi can provide
- Use externally-powered USB hub

**3. Check USB Cable**
- Use high-quality, shielded cable
- Keep cable short (<1.5m)
- Avoid running near motors/heaters

**4. Use by-path Paths**

Instead of:
```ini
serial: /dev/ttyACM0  # ← May change on reconnect
```

Use:
```ini
auto_detect: true     # ← Stable, uses by-path internally
```

---

## Serial Permission Denied

### Error
```
Permission denied: '/dev/ttyACM0'
```

### Fix

```bash
# Add user to groups
sudo usermod -a -G dialout $USER
sudo usermod -a -G tty $USER

# Logout and login (or reboot)
```

**Verify:**
```bash
# Check groups
groups | grep -E '(dialout|tty)'

# Should show both dialout and tty
```

**Temporary fix (testing only):**
```bash
sudo chmod 666 /dev/ttyACM0
# Note: Reverts on device reconnect
```

---

## Klipper Won't Start

### Symptoms
- Klipper fails to start
- Config errors in log

### Diagnostic Steps

**1. Check Klipper Log**
```bash
tail -50 ~/printer_data/logs/klippy.log
```

**2. Common Errors**

| Error | Solution |
|-------|----------|
| `Unknown config object 'AFC_ACE'` | Re-run install-afc-ace.sh |
| `ImportError: AFC_ACE_protocol` | Check symlinks in ~/klipper/klippy/extras/ |
| `AFC_hub not found` | See "AFC_hub not found" section above |

**3. Verify Symlinks**
```bash
ls -l ~/klipper/klippy/extras/AFC_ACE*.py
```

Should show symlinks to AFC-ACE-Integration/extras/

**4. Test Config Syntax**
```bash
# Comment out AFC-ACE temporarily
nano ~/printer_data/config/printer.cfg

# Comment this line:
# [include AFC/*.cfg]

# Restart Klipper
sudo systemctl restart klipper

# If starts OK, problem is in AFC config
# If still fails, problem is elsewhere
```

---

## Multi-ACE Issues

### Devices Swap Order on Reboot

**Symptom:** Lane 0-3 become lanes 4-7 after reboot

**Cause:** USB enumeration order changed

**Solution:** Use stable device_index

```ini
# Device index is based on USB port location, not enumeration order
[AFC_ACE ace1]
auto_detect: true
device_index: 0  # Always first USB port

[AFC_ACE ace2]
auto_detect: true
device_index: 1  # Always second USB port
```

**Keep ACE devices in same physical USB ports!**

### Can't Detect Second ACE

**Check detection:**
```bash
python3 ~/AFC-ACE-Integration/utilities/detect_ace_devices.py
```

**If only 1 device shown:**
1. Check second ACE is powered
2. Try different USB port
3. Check USB hub (if used)
4. Test with single ACE first

---

## Debug Mode

### Enable Verbose Logging

**In config:**
```ini
[AFC_ACE ace1]
auto_detect: true
extruder: extruder
log_level: DEBUG  # Add this line
```

**View logs:**
```bash
tail -f ~/printer_data/logs/klippy.log | grep AFC_ACE
```

### Test Commands

```gcode
# Get detailed status
ACE_GET_STATUS

# Manual feed test
ACE_FEED INDEX=0 LENGTH=10 SPEED=30

# Test sensor detection
QUERY_ENDSTOPS

# Check AFC status
AFC_STATUS
```

---

## Getting Help

When asking for help, include:

**1. Hardware Info**
```bash
# Run this and include output:
python3 ~/AFC-ACE-Integration/utilities/detect_ace_devices.py
```

**2. Config Files**
```bash
cat ~/printer_data/config/AFC/AFC_ACE_Pro.cfg
```

**3. Klipper Log**
```bash
tail -100 ~/printer_data/logs/klippy.log
```

**4. Error Messages**
- Exact error text
- When it occurs (startup, during print, tool change, etc.)

**5. System Info**
- Klipper host (Pi model, OS)
- Number of ACE devices
- USB connection type (direct, hub, etc.)

### Support Channels

- **AFC Discord:** https://discord.gg/eT8zc3bvPR
- **GitHub Issues:** https://github.com/mon5termatt/AFC-ACE-Integration/issues
- **AFC Docs:** https://armoredturtle.xyz/docs/

---

## Lanes Not Visible in UI

### Symptoms
- ACE units show up in Mainsail/Fluidd UI
- No lanes appear under the units
- Console shows "Prepping lanes" but lanes stay hidden

### Cause
Lanes need both `load: true` and `prep: true` to be visible in the UI. The `prep` flag is set when `system_Test()` successfully queries the ACE device status.

### Debug Steps

**1. Check if lanes exist in Klipper state:**
```bash
curl -s "http://your-printer.local:7125/printer/objects/query?AFC_lane%20leg1" | python3 -m json.tool
```

Look for `"prep": true` and `"load": true` - both must be true for lanes to show.

**2. Check for serial errors:**
```bash
tail -100 ~/printer_data/logs/klippy.log | grep "AFC_ACE.*Error"
```

If you see `Input/output error` or `write failed`, the ACE devices are not responding.

### Solution

**If serial errors are present:**

1. **Power cycle the ACE devices** - Turn them off and back on
2. **Restart Klipper** - `sudo systemctl restart klipper`
3. **Run prep** - Type `prep` (lowercase) in console

The serial communication with ACE devices can become unstable. If errors persist:

- Check USB cable quality (use data cables, not charge-only)
- Try a powered USB hub
- Check for USB power issues: `dmesg | grep -i usb`
- Reduce query frequency in code if needed

**Quick Test:**
```bash
# Test ACE communication directly
python3 ~/AFC-ACE-Integration/utilities/detect_ace_devices.py
```

If this shows devices but Klipper can't communicate, it's a serial port conflict or permission issue.

---

## Prevention

### Best Practices

✅ **DO:**
- Use `auto_detect: true` for stability
- Keep ACE devices in same USB ports
- Use quality USB cables
- Comment out optional config (hub, buffer)
- Run `check_config.py` before starting Klipper
- Test with `detect_ace_devices.py` first

❌ **DON'T:**
- Use `/dev/ttyACM*` paths (unstable)
- Specify hub without defining it
- Run as root
- Use charge-only USB cables
- Hot-plug ACE during prints

### Regular Maintenance

**Monthly:**
- Clean ACE filament paths
- Check USB connections
- Update AFC and AFC-ACE

**Before Important Prints:**
- Run `ACE_GET_STATUS`
- Test tool changes
- Verify filament loads
