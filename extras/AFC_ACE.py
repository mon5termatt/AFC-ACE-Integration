# Armored Turtle Automated Filament Control - ACE Unit
#
# Copyright (C) 2024 Armored Turtle
#
# This file may be distributed under the terms of the GNU GPLv3 license.

"""
ACE Pro Unit Driver for AFC
Implements AFC unit interface for ACE Pro hardware
"""

import traceback
import logging
import time
from configparser import Error as error
from datetime import datetime

# USB RPC attempts per user-facing G-code / critical protocol call
ACE_PROTO_RETRIES = 3

# Background reconnect when ACE is unplugged or USB is flaky
ACE_RECONNECT_BACKOFF_MIN = 5.0
ACE_RECONNECT_BACKOFF_MAX = 30.0
ACE_RECONNECT_BACKOFF_FACTOR = 1.5
ACE_RECONNECT_INITIAL_DELAY = 0.25

try: from extras.AFC_utils import ERROR_STR
except: raise error("Error when trying to import AFC_utils.ERROR_STR\n{trace}".format(trace=traceback.format_exc()))

try: from extras.AFC_lane import AFCLaneState
except: raise error(ERROR_STR.format(import_lib="AFC_lane", trace=traceback.format_exc()))

try: from extras.AFC_unit import afcUnit
except: raise error(ERROR_STR.format(import_lib="AFC_unit", trace=traceback.format_exc()))

try: from extras.AFC_ACE_protocol import AceProtocol
except: raise error(ERROR_STR.format(import_lib="AFC_ACE_protocol", trace=traceback.format_exc()))

try: from extras.AFC_ACE_discovery import AceDiscovery
except: raise error(ERROR_STR.format(import_lib="AFC_ACE_discovery", trace=traceback.format_exc()))


class afcACE(afcUnit):
    """
    ACE Pro unit driver for AFC.

    Implements the AFC unit interface to control ACE Pro hardware via USB protocol.
    Each ACE device provides 4 lanes (slots).
    """

    def __init__(self, config):
        """
        Initialize ACE unit.

        Args:
            config: Klipper configuration object
        """
        super().__init__(config)
        self.type = config.get('type', 'ACE')  # Changed from ACE_Pro to ACE to match class name AFC_ACE

        # ACE does NOT use a hub - it manages filament internally
        # Override the hub parameter from base class to prevent hub lookup errors
        self.hub = None

        # Create mock hub object to prevent lanes from auto-assigning to other hubs
        # AFC lanes check unit.hub_obj and auto-assign to first available hub if None
        # ACE manages filament internally, so hub operations are not needed
        class MockHub:
            def __init__(self):
                self.state = True     # Always "triggered" - ACE manages filament internally
                self.name = None      # No actual hub name
                self.lanes = {}       # Lanes will register here instead of real hub
                self.move_dis = 0     # Hub move distance (not used for ACE)
                self.load_length = 0  # Hub load length (not used for ACE)

        self.hub_obj = MockHub()

        # ACE-specific configuration
        self.serial = config.get('serial', None)
        self.auto_detect = config.getboolean('auto_detect', False)
        self.device_index = config.getint('device_index', 0)
        self.baud = config.getint('baud', 115200)

        # ACE protocol handler
        self.protocol = None
        self.device_id = None
        self.device_info = None

        # Lane to ACE slot mapping (AFC lane → ACE slot index)
        # ACE has 4 slots (0-3), each becomes a lane in AFC
        self.lane_to_slot = {}  # {lane_name: slot_index}

        # Status tracking
        self.last_status = None
        self.connected = False
        self._ace_connect_timer = None
        self._reconnect_backoff = ACE_RECONNECT_BACKOFF_MIN

        logging.info(f"AFC_ACE: Initialized unit '{self.name}'")

    def handle_connect(self):
        """
        Handle the connection event.
        Called when the printer connects.
        """
        super().handle_connect()

        # Banner for PREP / status (Mainsail success--text)
        _ace_banner = (
            "   █████╗   ██████╗ ███████╗\n"
            "  ██╔══██╗ ██╔════╝ ██╔════╝\n"
            "  ███████║ ██║      █████╗  \n"
            "  ██╔══██║ ██║      ██╔══╝  \n"
            "  ██║  ██║ ╚██████╗ ███████╗\n"
            "  ╚═╝  ╚═╝  ╚═════╝ ╚══════╝\n"
        )
        self.logo = (
            '<span class=success--text>'
            + _ace_banner
            + ("  %s\n" % self.name.center(36))
            + "</span>"
        )

        self.logo_error = (
            '<span class=error--text>'
            '   █████╗   ██████╗ ███████╗\n'
            '  ██╔══██╗ ██╔════╝ ██╔════╝\n'
            '  ███████║ ██║ <span class=secondary--text>X</span>  █████╗  \n'
            '  ██╔══██║ ██║      ██╔══╝  \n'
            '  ██║  ██║ ╚██████╗ ███████╗\n'
            '  ╚═╝  ╚═╝  ╚═════╝ ╚══════╝\n'
            + ("  %s\n" % self.name.center(36))
            + "</span>"
        )

        # Register ACE-specific G-code commands first so they exist while USB connects
        self._register_gcode_commands()

        # Deferred USB connect with retries
        self._reconnect_backoff = ACE_RECONNECT_BACKOFF_MIN
        self._ace_connect_timer = self.reactor.register_timer(
            self._ace_connect_timer_handler,
            self.reactor.monotonic() + ACE_RECONNECT_INITIAL_DELAY,
        )
        self.gcode.respond_info(
            "AFC_ACE: unit '%s' will connect in background (USB may enumerate late)"
            % self.name
        )

        # Note: Lane mapping is built lazily in get_slot_for_lane()
        # because lanes aren't registered to the unit until after handle_connect()

    def _cleanup_failed_connect(self):
        """Drop protocol state after a failed or partial connect."""
        self.connected = False
        self.device_info = None
        if self.protocol:
            try:
                self.protocol.disconnect()
            except Exception:
                pass
            self.protocol = None

    def _connect_ace_sync(self):
        """
        Single connect attempt. Returns True if fully online.
        Does not raise on I/O or missing device — returns False so timers can retry.
        Raises configparser.Error only for unrecoverable misconfiguration.
        """
        if not self.auto_detect and not self.serial:
            raise error(
                "AFC_ACE: No serial port for unit '%s'. Set 'serial' or enable 'auto_detect'"
                % self.name
            )

        if self.auto_detect:
            logging.info("AFC_ACE: Auto-detecting ACE devices...")
            devices = AceDiscovery.find_ace_devices()
            if not devices:
                logging.warning("AFC_ACE: No ACE devices found (auto_detect)")
                return False
            if self.device_index >= len(devices):
                raise error(
                    "AFC_ACE: Device index %s out of range (found %s devices)"
                    % (self.device_index, len(devices))
                )
            device = devices[self.device_index]
            self.serial = device["port"]
            self.device_id = device["device_id"]
            logging.info(
                "AFC_ACE: Auto-detected device %s: %s (ID: %s)"
                % (self.device_index, self.serial, self.device_id)
            )

        self._cleanup_failed_connect()

        try:
            self.protocol = AceProtocol(self.serial, self.baud)
            if not self.protocol.connect():
                logging.warning("AFC_ACE: Failed to open serial %s", self.serial)
                self._cleanup_failed_connect()
                return False

            self.device_info = self.protocol.get_info()
            if not self.device_info:
                logging.warning("AFC_ACE: get_info failed for %s", self.serial)
                self._cleanup_failed_connect()
                return False

            self.connected = True
            model = self.device_info.get("model", "Unknown")
            firmware = self.device_info.get("firmware", "Unknown")
            logging.info(
                "AFC_ACE: ✓ Connected to %s (FW: %s) at %s"
                % (model, firmware, self.serial)
            )

            time.sleep(0.2)
            self.last_status = self.protocol.get_status()
            if not self.last_status:
                logging.warning(
                    "AFC_ACE: Could not get initial status from %s, will retry later",
                    self.serial,
                )
            return True
        except error:
            raise
        except Exception as e:
            logging.warning("AFC_ACE: Connect attempt failed for '%s': %s", self.name, e)
            self._cleanup_failed_connect()
            return False

    def _schedule_reconnect_timer(self, initial_delay):
        """Arm background reconnect if nothing is scheduled yet."""
        if self.connected or self._ace_connect_timer is not None:
            return
        wake = self.reactor.monotonic() + initial_delay
        self._ace_connect_timer = self.reactor.register_timer(
            self._ace_connect_timer_handler, wake
        )

    def _ace_connect_timer_handler(self, eventtime):
        """Keep trying USB until connected, with exponential backoff."""
        try:
            if self.connected:
                self._ace_connect_timer = None
                return self.reactor.NEVER

            if self._connect_ace_sync():
                self._reconnect_backoff = ACE_RECONNECT_BACKOFF_MIN
                model = self.device_info.get("model", "Unknown")
                firmware = self.device_info.get("firmware", "Unknown")
                self.gcode.respond_info(
                    "AFC_ACE: unit '%s' connected — %s (FW: %s) at %s"
                    % (self.name, model, firmware, self.serial)
                )
                self._ace_connect_timer = None
                return self.reactor.NEVER

            backoff = self._reconnect_backoff
            nxt = self._reconnect_backoff * ACE_RECONNECT_BACKOFF_FACTOR
            if nxt >= ACE_RECONNECT_BACKOFF_MAX:
                self._reconnect_backoff = ACE_RECONNECT_BACKOFF_MIN
            else:
                self._reconnect_backoff = nxt

            portmsg = self.serial or "(auto-detect: no device yet)"
            self.gcode.respond_info(
                "AFC_ACE: unit '%s' not reachable (%s); retry in %.0fs"
                % (self.name, portmsg, backoff)
            )
            return eventtime + backoff
        except error as e:
            logging.exception("AFC_ACE: config error for unit '%s'", self.name)
            self.gcode.respond_info("AFC_ACE: unit '%s' config error: %s" % (self.name, e))
            self._ace_connect_timer = None
            self._cleanup_failed_connect()
            return self.reactor.NEVER

    def _ensure_connected(self):
        """
        Ensure the unit has an open, working serial connection.

        ACE devices may briefly disconnect/re-enumerate on USB (power saving,
        hubs, flaky cables). If the underlying serial port disappears, attempt
        to re-discover and reconnect when auto_detect is enabled.
        """
        try:
            if (
                self.connected
                and self.protocol
                and getattr(self.protocol, "serial", None)
                and self.protocol.serial.is_open
            ):
                return
            self.connected = False
            if self._connect_ace_sync():
                return
            self._schedule_reconnect_timer(self._reconnect_backoff)
            raise error(
                "AFC_ACE: unit '%s' not connected (USB); background retry scheduled"
                % self.name
            )
        except error:
            raise
        except Exception as e:
            raise error(f"AFC_ACE: Unable to (re)connect unit '{self.name}': {e}")

    def _proto_retry(self, label, fn):
        """
        Run a protocol callable until it returns a truthy value or attempts exhausted.
        Reconnects between attempts to recover from transient USB I/O errors.
        """
        for attempt in range(ACE_PROTO_RETRIES):
            try:
                self._ensure_connected()
                res = fn()
                if res:
                    return res
            except Exception as e:
                logging.warning(
                    "AFC_ACE: %s (unit=%s) attempt %s/%s failed: %s",
                    label, self.name, attempt + 1, ACE_PROTO_RETRIES, e,
                )
            if attempt + 1 < ACE_PROTO_RETRIES:
                time.sleep(0.2 * (attempt + 1))
        return None

    def _respond_ok(self, cmd_name, detail=None):
        """Uniform SUCCESS line for completed G-code commands."""
        if detail:
            self.afc.gcode.respond_info(
                "%s: SUCCESS unit=%s — %s" % (cmd_name, self.name, detail)
            )
        else:
            self.afc.gcode.respond_info("%s: SUCCESS unit=%s" % (cmd_name, self.name))

    def _cmd_guard(self, label: str, fn):
        """
        Run a gcode handler safely.
        If something goes wrong (USB disconnect, protocol error, bad params),
        report it to the console instead of crashing Klipper.
        """
        try:
            return fn()
        except Exception as e:
            logging.exception("AFC_ACE: %s failed for unit '%s'", label, self.name)
            self.afc.gcode.respond_info(f"{label}: FAILED for unit '{self.name}': {e}")
            return None

    # ============================================================
    # GCode commands
    # ============================================================

    def _register_gcode_commands(self):
        # Per-unit mux commands (required for multi-ACE setups)
        self.gcode.register_mux_command(
            "ACE_GET_STATUS", "UNIT", self.name, self.cmd_ACE_GET_STATUS,
            desc="Show ACE status for a specific unit (UNIT=<name>)"
        )
        self.gcode.register_mux_command(
            "ACE_FEED", "UNIT", self.name, self.cmd_ACE_FEED,
            desc="Feed filament from an ACE slot (UNIT=<name> INDEX=0-3 LENGTH=<mm> SPEED=10-80)"
        )
        self.gcode.register_mux_command(
            "ACE_RETRACT", "UNIT", self.name, self.cmd_ACE_RETRACT,
            desc="Retract filament back into an ACE slot (UNIT=<name> INDEX=0-3 LENGTH=<mm> SPEED=10-80)"
        )
        self.gcode.register_mux_command(
            "ACE_START_DRYING", "UNIT", self.name, self.cmd_ACE_START_DRYING,
            desc="Start ACE dryer (UNIT=<name> TEMP=<c> DURATION=<minutes>)"
        )
        self.gcode.register_mux_command(
            "ACE_STOP_DRYING", "UNIT", self.name, self.cmd_ACE_STOP_DRYING,
            desc="Stop ACE dryer (UNIT=<name>)"
        )
        self.gcode.register_mux_command(
            "ACE_ENABLE_FEED_ASSIST", "UNIT", self.name, self.cmd_ACE_ENABLE_FEED_ASSIST,
            desc="Enable ACE feed assist for a slot (UNIT=<name> LANE=0-3)"
        )
        self.gcode.register_mux_command(
            "ACE_DISABLE_FEED_ASSIST", "UNIT", self.name, self.cmd_ACE_DISABLE_FEED_ASSIST,
            desc="Disable ACE feed assist for a slot (UNIT=<name> LANE=0-3)"
        )

        self.gcode.register_mux_command(
            "ACE_ENABLE_RFID", "UNIT", self.name, self.cmd_ACE_ENABLE_RFID,
            desc="Enable ACE RFID reader (UNIT=<name>)"
        )
        self.gcode.register_mux_command(
            "ACE_DISABLE_RFID", "UNIT", self.name, self.cmd_ACE_DISABLE_RFID,
            desc="Disable ACE RFID reader (UNIT=<name>)"
        )
        self.gcode.register_mux_command(
            "ACE_GET_FILAMENT_INFO", "UNIT", self.name, self.cmd_ACE_GET_FILAMENT_INFO,
            desc="Query RFID filament info for a slot (UNIT=<name> INDEX=0-3)"
        )

        # Note: Do not also register non-mux commands with the same names.
        # Klipper treats the mux base command as the command name, so registering
        # both would halt with "gcode command ... already registered".

    def cmd_ACE_GET_STATUS(self, gcmd):
        return self._cmd_guard("ACE_GET_STATUS", lambda: self._cmd_ACE_GET_STATUS(gcmd))

    def _cmd_ACE_GET_STATUS(self, gcmd):
        self._ensure_connected()

        status = self._proto_retry("get_status", self.protocol.get_status)
        info = self.device_info or {}

        if not status:
            self.afc.gcode.respond_info(f"ACE_GET_STATUS: No response from unit '{self.name}' ({self.serial})")
            return

        model = info.get("model", "Unknown")
        firmware = info.get("firmware", "Unknown")
        dryer = status.get("dryer", status.get("dryer_status", None))
        temp = status.get("temp", None)
        slots = status.get("slots", None)

        lines = [
            f"ACE unit: {self.name}",
            f"  Port: {self.serial}",
            f"  Model: {model}",
            f"  Firmware: {firmware}",
        ]
        if dryer is not None:
            lines.append(f"  Dryer: {dryer}")
        if temp is not None:
            lines.append(f"  Temp: {temp}")
        if isinstance(slots, list):
            lines.append(f"  Slots: {slots}")
        else:
            lines.append("  Slots: (unavailable)")

        self.afc.gcode.respond_info("\n".join(lines))
        self._respond_ok("ACE_GET_STATUS")

    def _parse_index_speed_len(self, gcmd):
        index = gcmd.get_int("INDEX", None)
        if index is None:
            index = gcmd.get_int("LANE", None)
        if index is None:
            raise error("AFC_ACE: Missing INDEX (or LANE) parameter")
        if index < 0 or index > 3:
            raise error("AFC_ACE: INDEX must be 0-3 for an ACE unit")

        length = gcmd.get_float("LENGTH", None)
        if length is None:
            raise error("AFC_ACE: Missing LENGTH parameter")
        if length <= 0:
            raise error("AFC_ACE: LENGTH must be > 0")

        speed = gcmd.get_int("SPEED", 50)
        speed = max(10, min(80, int(speed)))
        return index, length, speed

    def cmd_ACE_FEED(self, gcmd):
        return self._cmd_guard("ACE_FEED", lambda: self._cmd_ACE_FEED(gcmd))

    def _cmd_ACE_FEED(self, gcmd):
        self._ensure_connected()
        index, length, speed = self._parse_index_speed_len(gcmd)
        ok = self._proto_retry(
            "feed",
            lambda: self.protocol.feed(index, length, speed),
        )
        if not ok:
            raise error(f"AFC_ACE: ACE_FEED failed (unit={self.name} index={index})")
        self._respond_ok(
            "ACE_FEED",
            "index=%s length=%s speed=%s" % (index, int(length), speed),
        )

    def cmd_ACE_RETRACT(self, gcmd):
        return self._cmd_guard("ACE_RETRACT", lambda: self._cmd_ACE_RETRACT(gcmd))

    def _cmd_ACE_RETRACT(self, gcmd):
        self._ensure_connected()
        index, length, speed = self._parse_index_speed_len(gcmd)
        ok = self._proto_retry(
            "retract",
            lambda: self.protocol.retract(index, length, speed),
        )
        if not ok:
            raise error(f"AFC_ACE: ACE_RETRACT failed (unit={self.name} index={index})")
        self._respond_ok(
            "ACE_RETRACT",
            "index=%s length=%s speed=%s" % (index, int(length), speed),
        )

    def cmd_ACE_START_DRYING(self, gcmd):
        return self._cmd_guard("ACE_START_DRYING", lambda: self._cmd_ACE_START_DRYING(gcmd))

    def _cmd_ACE_START_DRYING(self, gcmd):
        self._ensure_connected()
        temp = gcmd.get_int("TEMP", 55)
        duration = gcmd.get_int("DURATION", 240)
        ok = self._proto_retry(
            "start_dryer",
            lambda: self.protocol.start_dryer(temp, duration),
        )
        if not ok:
            raise error(f"AFC_ACE: ACE_START_DRYING failed (unit={self.name})")
        # Immediately re-query status so users can verify it actually started.
        time.sleep(0.2)
        status = self._proto_retry("get_status", self.protocol.get_status) or {}
        dryer = status.get("dryer", status.get("dryer_status", None))
        cur_temp = status.get("temp", None)
        self._respond_ok(
            "ACE_START_DRYING",
            "temp=%s duration=%smin (reported dryer=%s temp=%s)"
            % (temp, duration, dryer, cur_temp),
        )

    def cmd_ACE_STOP_DRYING(self, gcmd):
        return self._cmd_guard("ACE_STOP_DRYING", lambda: self._cmd_ACE_STOP_DRYING(gcmd))

    def _cmd_ACE_STOP_DRYING(self, gcmd):
        self._ensure_connected()
        ok = self._proto_retry("stop_dryer", self.protocol.stop_dryer)
        if not ok:
            raise error(f"AFC_ACE: ACE_STOP_DRYING failed (unit={self.name})")
        self._respond_ok("ACE_STOP_DRYING")

    def cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        return self._cmd_guard("ACE_ENABLE_FEED_ASSIST", lambda: self._cmd_ACE_ENABLE_FEED_ASSIST(gcmd))

    def _cmd_ACE_ENABLE_FEED_ASSIST(self, gcmd):
        self._ensure_connected()
        lane = gcmd.get_int("LANE", None)
        if lane is None:
            lane = gcmd.get_int("INDEX", None)
        if lane is None:
            raise error("AFC_ACE: Missing LANE (or INDEX) parameter")
        if lane < 0 or lane > 3:
            raise error("AFC_ACE: LANE must be 0-3 for an ACE unit")
        ok = self._proto_retry(
            "set_feed_assist_enable",
            lambda: self.protocol.set_feed_assist(lane, True),
        )
        if not ok:
            raise error(f"AFC_ACE: ACE_ENABLE_FEED_ASSIST failed (unit={self.name} lane={lane})")
        self._respond_ok("ACE_ENABLE_FEED_ASSIST", "lane=%s" % lane)

    def cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        return self._cmd_guard("ACE_DISABLE_FEED_ASSIST", lambda: self._cmd_ACE_DISABLE_FEED_ASSIST(gcmd))

    def _cmd_ACE_DISABLE_FEED_ASSIST(self, gcmd):
        self._ensure_connected()
        lane = gcmd.get_int("LANE", None)
        if lane is None:
            lane = gcmd.get_int("INDEX", None)
        if lane is None:
            raise error("AFC_ACE: Missing LANE (or INDEX) parameter")
        if lane < 0 or lane > 3:
            raise error("AFC_ACE: LANE must be 0-3 for an ACE unit")
        ok = self._proto_retry(
            "set_feed_assist_disable",
            lambda: self.protocol.set_feed_assist(lane, False),
        )
        if not ok:
            raise error(f"AFC_ACE: ACE_DISABLE_FEED_ASSIST failed (unit={self.name} lane={lane})")
        self._respond_ok("ACE_DISABLE_FEED_ASSIST", "lane=%s" % lane)

    def cmd_ACE_ENABLE_RFID(self, gcmd):
        return self._cmd_guard("ACE_ENABLE_RFID", lambda: self._cmd_ACE_ENABLE_RFID(gcmd))

    def _cmd_ACE_ENABLE_RFID(self, gcmd):
        self._ensure_connected()
        ok = self._proto_retry("enable_rfid", self.protocol.enable_rfid)
        if not ok:
            raise error(f"AFC_ACE: ACE_ENABLE_RFID failed (unit={self.name})")
        self._respond_ok("ACE_ENABLE_RFID")

    def cmd_ACE_DISABLE_RFID(self, gcmd):
        return self._cmd_guard("ACE_DISABLE_RFID", lambda: self._cmd_ACE_DISABLE_RFID(gcmd))

    def _cmd_ACE_DISABLE_RFID(self, gcmd):
        self._ensure_connected()
        ok = self._proto_retry("disable_rfid", self.protocol.disable_rfid)
        if not ok:
            raise error(f"AFC_ACE: ACE_DISABLE_RFID failed (unit={self.name})")
        self._respond_ok("ACE_DISABLE_RFID")

    def cmd_ACE_GET_FILAMENT_INFO(self, gcmd):
        return self._cmd_guard("ACE_GET_FILAMENT_INFO", lambda: self._cmd_ACE_GET_FILAMENT_INFO(gcmd))

    def _cmd_ACE_GET_FILAMENT_INFO(self, gcmd):
        self._ensure_connected()
        index = gcmd.get_int("INDEX", None)
        if index is None:
            index = gcmd.get_int("LANE", None)
        if index is None:
            raise error("AFC_ACE: Missing INDEX (or LANE) parameter")
        if index < 0 or index > 3:
            raise error("AFC_ACE: INDEX must be 0-3 for an ACE unit")

        info = self._proto_retry(
            "get_filament_info",
            lambda: self.protocol.get_filament_info(index),
        )
        if not info:
            self.afc.gcode.respond_info(f"ACE_GET_FILAMENT_INFO: No response (unit={self.name} index={index})")
            return
        # Print as JSON so it can be copy/pasted easily.
        import json
        self.afc.gcode.respond_info(f"ACE_GET_FILAMENT_INFO: {json.dumps(info, sort_keys=True)}")
        self._respond_ok("ACE_GET_FILAMENT_INFO", "index=%s" % index)

    def _build_lane_mapping(self):
        """Build mapping between AFC lanes and ACE slots"""
        # Each lane in this unit corresponds to an ACE slot (0-3)
        # Lanes are registered with unit like "unit_name:slot_index"

        if not self.lanes:
            logging.debug(f"AFC_ACE: No lanes registered yet for unit '{self.name}', skipping mapping")
            return

        for lane in self.lanes.values():
            # Extract slot index from lane's unit specification
            # Lane config: unit: ace1:0  means slot 0 of unit ace1
            slot_index = lane.index  # This comes from unit:index in config

            self.lane_to_slot[lane.name] = slot_index

            logging.info(f"AFC_ACE: Mapped lane '{lane.name}' → slot {slot_index}")

    def get_slot_for_lane(self, lane) -> int:
        """
        Get ACE slot index for a lane.

        Args:
            lane: AFC lane object or lane name

        Returns:
            ACE slot index (0-3)
        """
        # Build mapping lazily if not done yet
        if not self.lane_to_slot and self.lanes:
            self._build_lane_mapping()

        lane_name = lane if isinstance(lane, str) else lane.name

        if lane_name not in self.lane_to_slot:
            raise error(f"AFC_ACE: Lane '{lane_name}' not mapped to ACE slot")

        return self.lane_to_slot[lane_name]

    def move_lane(self, lane, distance: float, speed: int, assist: bool = False):
        """
        Move filament in a lane.

        This is called by AFC lane.move() operations.
        Translates to ACE feed/retract commands.

        Args:
            lane: AFC lane object
            distance: Distance in mm (positive=feed, negative=retract)
            speed: Speed (10-80)
            assist: Enable feed assist during move
        """
        slot = self.get_slot_for_lane(lane)

        # Clamp speed to ACE limits (10-80)
        speed = max(10, min(80, int(speed)))

        try:
            if distance > 0:
                # Feed forward
                logging.debug(f"AFC_ACE: Feed slot {slot} distance {distance}mm speed {speed}")

                if assist:
                    self.protocol.set_feed_assist(slot, True)

                success = self.protocol.feed(slot, distance, speed)

                if not success:
                    logging.error(f"AFC_ACE: Feed command failed for slot {slot}")

            else:
                # Retract backward
                logging.debug(f"AFC_ACE: Retract slot {slot} distance {abs(distance)}mm speed {speed}")

                success = self.protocol.retract(slot, abs(distance), speed)

                if not success:
                    logging.error(f"AFC_ACE: Retract command failed for slot {slot}")

        except Exception as e:
            logging.error(f"AFC_ACE: Error moving lane '{lane.name}': {e}")
            raise

    def get_lane_status(self, lane) -> str:
        """
        Get status of a lane.

        Args:
            lane: AFC lane object

        Returns:
            Status string ('empty', 'ready', 'error', etc.)
        """
        slot = self.get_slot_for_lane(lane)

        try:
            # Get status from ACE
            status = self.protocol.get_status()

            if status and 'slots' in status:
                slot_status = status['slots'][slot]['status']

                # Map ACE status to AFC lane state
                # ACE statuses: 'empty', 'ready', 'loading', 'error'
                return slot_status

            return 'unknown'

        except Exception as e:
            logging.error(f"AFC_ACE: Error getting lane status: {e}")
            return 'error'

    def enable_feed_assist(self, lane, enable: bool = True):
        """
        Enable/disable feed assist for a lane.

        Args:
            lane: AFC lane object
            enable: True to enable, False to disable
        """
        slot = self.get_slot_for_lane(lane)

        try:
            success = self.protocol.set_feed_assist(slot, enable)

            if success:
                logging.debug(f"AFC_ACE: Feed assist {'enabled' if enable else 'disabled'} for slot {slot}")
            else:
                logging.warning(f"AFC_ACE: Failed to set feed assist for slot {slot}")

        except Exception as e:
            logging.error(f"AFC_ACE: Error setting feed assist: {e}")

    def start_dryer(self, temp: int, duration: int = 240):
        """
        Start ACE dryer.

        Args:
            temp: Target temperature in Celsius
            duration: Duration in minutes (default 240)
        """
        try:
            success = self.protocol.start_dryer(temp, duration)

            if success:
                logging.info(f"AFC_ACE: Dryer started at {temp}°C for {duration} minutes")
            else:
                logging.warning(f"AFC_ACE: Failed to start dryer")

        except Exception as e:
            logging.error(f"AFC_ACE: Error starting dryer: {e}")

    def stop_dryer(self):
        """Stop ACE dryer"""
        try:
            success = self.protocol.stop_dryer()

            if success:
                logging.info(f"AFC_ACE: Dryer stopped")
            else:
                logging.warning(f"AFC_ACE: Failed to stop dryer")

        except Exception as e:
            logging.error(f"AFC_ACE: Error stopping dryer: {e}")

    def system_Test(self, cur_lane, delay, assignTcmd, enable_movement):
        """
        System test for ACE lane.

        This is called by AFC's PREP command to test lanes.
        For ACE, we query the device status instead of physical sensor tests.

        Args:
            cur_lane: Lane to test
            delay: Delay between test steps
            assignTcmd: Whether to assign T command
            enable_movement: Whether to enable movement (for ACE, we just query status)
        """
        # Ensure lane mapping is built (lazy initialization)
        if not self.lane_to_slot and self.lanes:
            self._build_lane_mapping()

        msg = ''
        succeeded = True

        slot = self.get_slot_for_lane(cur_lane)

        try:
            # Get ACE slot status
            status = self.protocol.get_status()

            if not status or 'slots' not in status:
                # Communication error - but don't fail entirely, mark lane as unknown
                logging.warning(f"AFC_ACE: Could not get status for lane '{cur_lane.name}' (slot {slot})")
                self.afc.function.afc_led(cur_lane.led_not_ready, cur_lane.led_index)
                msg = "<span class=warning--text>UNKNOWN (Communication Error)</span>"
                cur_lane.status = AFCLaneState.NONE
                cur_lane.prep_state = True  # Set prep_state (exposed as 'prep' in API)
                cur_lane.load_state = True  # Set load_state (exposed as 'load' in API)
                logging.info(f"AFC_ACE: Set lane '{cur_lane.name}' prep_state={cur_lane.prep_state} load_state={cur_lane.load_state}")
                succeeded = True  # Don't fail prep for communication errors
                return msg, succeeded

            slot_info = status['slots'][slot]
            slot_status = slot_info['status']

            # Map ACE status to AFC states
            if slot_status == 'empty':
                self.afc.function.afc_led(cur_lane.led_not_ready, cur_lane.led_index)
                msg = 'EMPTY READY FOR SPOOL'
                cur_lane.status = AFCLaneState.NONE
                cur_lane.prep_state = True  # Lane is prepped (empty, ready for spool)
                cur_lane.load_state = True
                succeeded = True

            elif slot_status == 'ready':
                self.afc.function.afc_led(cur_lane.led_ready, cur_lane.led_index)
                msg = "<span class=success--text>LOCKED AND LOADED</span>"
                cur_lane.status = AFCLaneState.LOADED
                cur_lane.prep_state = True  # Lane is prepped (filament loaded and ready)
                cur_lane.load_state = True
                succeeded = True

                # Illuminate spool LED
                self.afc.function.afc_led(cur_lane.led_spool_illum, cur_lane.led_spool_index)

                # Check if loaded into toolhead
                if cur_lane.tool_loaded:
                    if cur_lane.extruder_obj and cur_lane.extruder_obj.lane_loaded == cur_lane.name:
                        self.afc.current = cur_lane.name
                        msg += "<span class=primary--text> in ToolHead</span>"

                        if self.afc.function.get_current_lane() == cur_lane.name:
                            self.afc.spool.set_active_spool(cur_lane.spool_id)
                            cur_lane.unit_obj.lane_tool_loaded(cur_lane)
                            cur_lane.status = AFCLaneState.TOOLED

            elif slot_status == 'error':
                self.afc.function.afc_led(cur_lane.led_fault, cur_lane.led_index)
                msg = "<span class=error--text>SLOT ERROR</span>"
                cur_lane.status = AFCLaneState.ERROR
                succeeded = False

            else:
                # Unknown status
                self.afc.function.afc_led(cur_lane.led_fault, cur_lane.led_index)
                msg = f"<span class=warning--text>UNKNOWN STATUS: {slot_status}</span>"
                succeeded = False

        except Exception as e:
            logging.error(f"AFC_ACE: Error during system test: {e}")
            self.afc.function.afc_led(cur_lane.led_fault, cur_lane.led_index)
            msg = "<span class=error--text>TEST ERROR</span>"
            succeeded = False

        return msg, succeeded

    def lane_tool_loaded(self, cur_lane):
        """
        Called when a lane is successfully loaded into the toolhead.

        Args:
            cur_lane: Lane that was loaded
        """
        # Update LED to show tool loaded
        if cur_lane.led_index is not None:
            self.afc.function.afc_led(cur_lane.led_tool_loaded, cur_lane.led_index)

        logging.info(f"AFC_ACE: Lane '{cur_lane.name}' loaded into toolhead")

    def calibrate_lane(self, cur_lane, tol):
        """
        Calibrate a lane.

        For ACE Pro, calibration is not needed since the device manages
        filament positioning internally via its protocol.

        Args:
            cur_lane: Lane to calibrate
            tol: Tolerance (unused for ACE)

        Returns:
            Tuple of (checked, msg, pos)
        """
        slot = self.get_slot_for_lane(cur_lane)

        # ACE handles filament positioning internally, no calibration needed
        msg = f"ACE Pro slot {slot} does not require calibration - device manages filament internally"

        self.afc.gcode.respond_info(msg)
        logging.info(f"AFC_ACE: {msg}")

        # Return success with current position (ACE manages this internally)
        return True, msg, 0.0

    def calibrate_bowden(self, cur_lane, dis, tol):
        """
        Calibrate bowden tube length.

        ACE Pro manages filament path internally, no bowden calibration needed.

        Args:
            cur_lane: Lane to calibrate
            dis: Distance (unused)
            tol: Tolerance (unused)

        Returns:
            Tuple of (checked, msg, pos)
        """
        msg = "ACE Pro does not require bowden calibration - device manages filament path"
        self.afc.gcode.respond_info(msg)
        return True, msg, 0.0

    def calibrate_hub(self, cur_lane, tol):
        """
        Calibrate hub distance.

        ACE Pro does not use a hub - it manages filament internally.

        Args:
            cur_lane: Lane to calibrate
            tol: Tolerance (unused)

        Returns:
            Tuple of (checked, msg, pos)
        """
        msg = "ACE Pro does not use a hub - no hub calibration needed"
        self.afc.gcode.respond_info(msg)
        return True, msg, 0.0


def load_config_prefix(config):
    """Klipper load function for [AFC_ACE name] sections"""
    return afcACE(config)

# Also support direct loading for backwards compatibility
load_config = load_config_prefix
