#!/usr/bin/env python3
"""
Pointer Tuner - a small GUI for xinput / libinput pointing-device settings
============================================================================

A desktop tool (Tkinter) for tuning relative pointing devices - TrackPoints
/ pointing "nubs", touchpads, trackballs, plain mice - through the X11
`xinput` utility and the `libinput` driver properties it exposes.

WHAT THIS CONTROLS
------------------
    Sensitivity          -> "libinput Accel Speed"            (-1.0 .. 1.0)
    Acceleration curve    -> "libinput Accel Profile Enabled"  (Adaptive/Flat)
    Natural scrolling     -> "libinput Natural Scrolling Enabled"
    Click-and-scroll      -> "libinput Scroll Method Enabled"  (button method)
                              + "libinput Button Scrolling Button"
    Middle-click emulation-> "libinput Middle Emulation Enabled"
    Left-handed mode      -> "libinput Left Handed Enabled"
    Enable / disable      -> "Device Enabled"

REQUIREMENTS
------------
    * Linux with an X11 session (plain Xorg, or a Wayland compositor that
      runs these apps through XWayland). Changes made through xinput only
      affect X11/XWayland clients, never Wayland-native applications.
    * The `xinput` command-line tool installed (package `xorg-xinput`,
      `xinput`, or similar, depending on your distro).
    * Python 3 with Tk support (`python3-tk` on Debian/Ubuntu,
      `python3-tkinter` on Fedora, `tk` on Arch).

USAGE
-----
    chmod +x pointer_tuner.py
    ./pointer_tuner.py

Settings applied through xinput do NOT persist across reboots or logins
by themselves - they reset to hardware defaults every time your X session
starts. Once you're happy with a device's settings, use "Export Startup
Script" to save a small shell script, then add it to your session
autostart (e.g. ~/.xprofile, or your desktop environment's "Startup
Applications" tool) so it re-applies automatically at login.

NOTE ON KERNEL-LEVEL TRACKPOINT SETTINGS
-----------------------------------------
Some laptops (many ThinkPads) also expose TrackPoint "Sensitivity" and
"Speed" directly through the kernel driver, under
/sys/devices/platform/i8042/serio1/serio2/{sensitivity,speed}. That is a
*separate* mechanism from xinput/libinput and needs root to write. This
tool intentionally stays within xinput's scope. If libinput's "Accel
Speed" alone doesn't give you the range you want, that sysfs interface
is the other knob worth knowing about.
"""

import os
import re
import shutil
import subprocess
import sys
import tkinter as tk
from datetime import datetime
from tkinter import ttk, messagebox, filedialog

# --------------------------------------------------------------------- #
# xinput / libinput property names
# --------------------------------------------------------------------- #
PROP_ENABLED            = "Device Enabled"
PROP_ACCEL_SPEED        = "libinput Accel Speed"
PROP_ACCEL_PROFILE      = "libinput Accel Profile Enabled"
PROP_NATURAL_SCROLL     = "libinput Natural Scrolling Enabled"
PROP_LEFT_HANDED        = "libinput Left Handed Enabled"
PROP_SCROLL_METHOD      = "libinput Scroll Method Enabled"
PROP_SCROLL_BUTTON      = "libinput Button Scrolling Button"
PROP_MIDDLE_EMULATION   = "libinput Middle Emulation Enabled"

# Index of the "button scrolling" method within the 3-element
# Scroll Method Enabled / Available arrays: [two-finger, edge, button]
SCROLL_METHOD_BUTTON_INDEX = 2

DEVICE_LINE_RE = re.compile(r'^[^\w]*(.+?)\s+id=(\d+)\s+\[(slave|master)\s+pointer')
PROP_LINE_RE   = re.compile(r'^(.*)\s+\((\d+)\):\s*(.*)$')

EXPORT_PROPS = [
    # (property name, kind) - kind is just documentation here; export
    # writes whatever is currently cached for each of these, if present.
    PROP_ACCEL_SPEED,
    PROP_ACCEL_PROFILE,
    PROP_NATURAL_SCROLL,
    PROP_SCROLL_METHOD,
    PROP_SCROLL_BUTTON,
    PROP_MIDDLE_EMULATION,
    PROP_LEFT_HANDED,
]


class XInputError(RuntimeError):
    pass


class XInput:
    """Thin wrapper around the `xinput` command-line tool."""

    @staticmethod
    def available():
        return shutil.which("xinput") is not None

    @staticmethod
    def list_pointer_devices():
        result = subprocess.run(["xinput", "list"], capture_output=True, text=True)
        if result.returncode != 0:
            raise XInputError(result.stderr.strip() or "xinput list failed")
        devices = []
        for line in result.stdout.splitlines():
            m = DEVICE_LINE_RE.match(line)
            if not m:
                continue
            name, dev_id, kind = m.group(1).strip(), m.group(2), m.group(3)
            if "Virtual core" in name or "XTEST" in name:
                continue
            if kind != "slave":
                continue
            devices.append((name, dev_id))
        return devices

    @staticmethod
    def get_props(device_id):
        result = subprocess.run(["xinput", "list-props", str(device_id)],
                                 capture_output=True, text=True)
        if result.returncode != 0:
            raise XInputError(result.stderr.strip() or "xinput list-props failed")
        props = {}
        for line in result.stdout.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            m = PROP_LINE_RE.match(line)
            if not m:
                continue
            name, _code, values_str = m.group(1), m.group(2), m.group(3)
            values = [v.strip() for v in values_str.split(',')] if values_str else []
            props[name] = values
        return props

    @staticmethod
    def set_prop(device_id, prop_name, values):
        cmd = ["xinput", "set-prop", str(device_id), prop_name] + [str(v) for v in values]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise XInputError(result.stderr.strip() or "Failed to set '%s'" % prop_name)
        return cmd


class Tooltip:
    """A minimal hover tooltip for any Tk widget."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(self.tip, text=self.text, justify="left",
                          background="#2b2b2b", foreground="#f0f0f0",
                          relief="solid", borderwidth=1,
                          font=("TkDefaultFont", 9), padx=6, pady=3,
                          wraplength=280)
        label.pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class PointerTunerApp(tk.Tk):
    COLORS = {
        "bg": "#1e1f22",
        "panel": "#26282b",
        "accent": "#5fb3ff",
        "text": "#e8e9eb",
        "muted": "#9a9ea3",
        "ok": "#59c26a",
        "err": "#e0605a",
    }

    def __init__(self):
        super().__init__()
        self.title("Pointer Tuner \u2014 xinput / libinput")
        self.geometry("560x640")
        self.minsize(520, 560)
        self.configure(bg=self.COLORS["bg"])

        self.device_id = None
        self.device_name = None
        self.props = {}
        self.devices = []          # list[(name, id)]
        self._syncing = False      # guard against feedback loops while syncing UI

        self._build_style()
        self._build_layout()

        if not XInput.available():
            self._log("xinput was not found on this system's PATH.", level="err")
            messagebox.showerror(
                "xinput not found",
                "This tool needs the 'xinput' command-line utility.\n\n"
                "Install it with your package manager, e.g.:\n"
                "  sudo apt install x11-xserver-utils\n"
                "  sudo dnf install xorg-x11-server-utils\n"
                "  sudo pacman -S xorg-xinput",
            )
            self._set_controls_enabled(False)
            return

        if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            self._log(
                "Session type is Wayland: xinput will only affect X11/XWayland "
                "clients, not native Wayland apps.", level="warn"
            )

        self.refresh_devices()

    # ------------------------------------------------------------ style ---
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        c = self.COLORS
        style.configure(".", background=c["bg"], foreground=c["text"],
                         fieldbackground=c["panel"])
        style.configure("TFrame", background=c["bg"])
        style.configure("Panel.TFrame", background=c["panel"])
        style.configure("TLabelframe", background=c["bg"], foreground=c["text"],
                         bordercolor=c["muted"])
        style.configure("TLabelframe.Label", background=c["bg"],
                         foreground=c["accent"], font=("TkDefaultFont", 10, "bold"))
        style.configure("TLabel", background=c["bg"], foreground=c["text"])
        style.configure("Muted.TLabel", background=c["bg"], foreground=c["muted"],
                         font=("TkDefaultFont", 9))
        style.configure("TCheckbutton", background=c["bg"], foreground=c["text"])
        style.map("TCheckbutton", background=[("active", c["bg"])])
        style.configure("TRadiobutton", background=c["bg"], foreground=c["text"])
        style.map("TRadiobutton", background=[("active", c["bg"])])
        style.configure("TButton", padding=6)
        style.configure("TNotebook", background=c["bg"], bordercolor=c["bg"])
        style.configure("TNotebook.Tab", padding=(12, 6))
        style.configure("Horizontal.TScale", background=c["bg"])
        style.configure("TCombobox", fieldbackground=c["panel"], background=c["panel"])
        style.configure("TSpinbox", fieldbackground=c["panel"], background=c["panel"])

    # ----------------------------------------------------------- layout ---
    def _build_layout(self):
        pad = 10

        header = ttk.Frame(self)
        header.pack(fill="x", padx=pad, pady=(pad, 4))
        ttk.Label(header, text="Device:", font=("TkDefaultFont", 10, "bold")).pack(side="left")
        self.device_var = tk.StringVar()
        self.device_combo = ttk.Combobox(header, textvariable=self.device_var,
                                          state="readonly", width=34)
        self.device_combo.pack(side="left", padx=(8, 8))
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_selected)
        ttk.Button(header, text="\u21bb Rescan", command=self.refresh_devices).pack(side="left")

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=pad, pady=4)

        self.tab_pointer = ttk.Frame(self.notebook, padding=12)
        self.tab_scroll = ttk.Frame(self.notebook, padding=12)
        self.tab_test = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(self.tab_pointer, text="Sensitivity / Acceleration")
        self.notebook.add(self.tab_scroll, text="Scrolling & Buttons")
        self.notebook.add(self.tab_test, text="Test Area")

        self._build_pointer_tab()
        self._build_scroll_tab()
        self._build_test_tab()

        # ---- bottom bar: log + actions ----
        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=pad, pady=(4, pad))

        log_frame = ttk.LabelFrame(bottom, text="Log")
        log_frame.pack(fill="x", pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=5, bg=self.COLORS["panel"],
                                 fg=self.COLORS["text"], insertbackground=self.COLORS["text"],
                                 relief="flat", padx=6, pady=4, font=("TkFixedFont", 9),
                                 state="disabled", wrap="word")
        self.log_text.pack(fill="x")
        self.log_text.tag_config("err", foreground=self.COLORS["err"])
        self.log_text.tag_config("ok", foreground=self.COLORS["ok"])
        self.log_text.tag_config("warn", foreground="#e0b34a")

        actions = ttk.Frame(bottom)
        actions.pack(fill="x")
        ttk.Button(actions, text="Reset to Defaults",
                   command=self.reset_defaults).pack(side="left")
        ttk.Button(actions, text="Export Startup Script",
                   command=self.export_script).pack(side="left", padx=8)
        ttk.Button(actions, text="Quit", command=self.destroy).pack(side="right")

    def _build_pointer_tab(self):
        t = self.tab_pointer

        sens = ttk.LabelFrame(t, text="Sensitivity", padding=10)
        sens.pack(fill="x", pady=(0, 12))
        row = ttk.Frame(sens)
        row.pack(fill="x")
        ttk.Label(row, text="Slow").pack(side="left")
        self.speed_var = tk.DoubleVar(value=0.0)
        self.speed_scale = ttk.Scale(row, from_=-1.0, to=1.0, orient="horizontal",
                                      variable=self.speed_var, command=self._on_speed_drag)
        self.speed_scale.pack(side="left", fill="x", expand=True, padx=8)
        ttk.Label(row, text="Fast").pack(side="left")
        self.speed_scale.bind("<ButtonRelease-1>", self._apply_speed)
        self.speed_label = ttk.Label(sens, text="0.00", style="Muted.TLabel")
        self.speed_label.pack(anchor="e")
        Tooltip(self.speed_scale,
                "libinput Accel Speed. -1.0 = slowest, 0.0 = default, "
                "1.0 = fastest. This is the main pointer sensitivity control.")

        accel = ttk.LabelFrame(t, text="Acceleration", padding=10)
        accel.pack(fill="x", pady=(0, 12))
        self.profile_var = tk.StringVar(value="adaptive")
        rb1 = ttk.Radiobutton(accel, text="Adaptive (speeds up the faster you move)",
                               variable=self.profile_var, value="adaptive",
                               command=self._apply_profile)
        rb2 = ttk.Radiobutton(accel, text="Flat (constant, 1:1 movement \u2014 no curve)",
                               variable=self.profile_var, value="flat",
                               command=self._apply_profile)
        rb1.pack(anchor="w")
        rb2.pack(anchor="w")
        Tooltip(rb2, "Many trackpoint users prefer Flat: predictable, "
                     "constant response instead of an acceleration curve.")
        self.profile_radios = [rb1, rb2]

        misc = ttk.LabelFrame(t, text="Orientation & Power", padding=10)
        misc.pack(fill="x")
        self.left_handed_var = tk.IntVar(value=0)
        self.left_handed_check = ttk.Checkbutton(
            misc, text="Left-handed (swap left/right buttons)",
            variable=self.left_handed_var, command=self._apply_left_handed)
        self.left_handed_check.pack(anchor="w")

        self.enabled_var = tk.IntVar(value=1)
        self.enabled_check = ttk.Checkbutton(
            misc, text="Device enabled", variable=self.enabled_var,
            command=self._apply_enabled)
        self.enabled_check.pack(anchor="w")
        Tooltip(self.enabled_check,
                "Turns this device's input on/off entirely. If you disable "
                "your only pointing device, use Tab + Space to switch it "
                "back on with the keyboard.")

    def _build_scroll_tab(self):
        t = self.tab_scroll

        scroll = ttk.LabelFrame(t, text="Scrolling", padding=10)
        scroll.pack(fill="x", pady=(0, 12))

        self.natural_var = tk.IntVar(value=0)
        self.natural_check = ttk.Checkbutton(
            scroll, text="Natural (reversed) scrolling",
            variable=self.natural_var, command=self._apply_natural)
        self.natural_check.pack(anchor="w")

        self.button_scroll_var = tk.IntVar(value=0)
        self.button_scroll_check = ttk.Checkbutton(
            scroll, text="Scroll by holding a button and moving the nub",
            variable=self.button_scroll_var, command=self._apply_scroll_method)
        self.button_scroll_check.pack(anchor="w", pady=(6, 0))
        Tooltip(self.button_scroll_check,
                "Classic TrackPoint-style scrolling: hold the chosen button "
                "and push the nub to scroll instead of move the pointer.")

        button_row = ttk.Frame(scroll)
        button_row.pack(anchor="w", padx=(24, 0), pady=(4, 0))
        ttk.Label(button_row, text="Button:").pack(side="left")
        self.scroll_button_var = tk.StringVar(value="2")
        self.scroll_button_combo = ttk.Combobox(
            button_row, textvariable=self.scroll_button_var, state="readonly",
            width=4, values=[str(n) for n in range(1, 10)])
        self.scroll_button_combo.pack(side="left", padx=6)
        self.scroll_button_combo.bind("<<ComboboxSelected>>", self._apply_scroll_button)
        ttk.Label(button_row, text="(2 = middle button, typical default)",
                  style="Muted.TLabel").pack(side="left")

        self.middle_emu_var = tk.IntVar(value=0)
        self.middle_emu_check = ttk.Checkbutton(
            scroll, text="Emulate middle-click (press left + right together)",
            variable=self.middle_emu_var, command=self._apply_middle_emulation)
        self.middle_emu_check.pack(anchor="w", pady=(10, 0))

    def _build_test_tab(self):
        t = self.tab_test
        ttk.Label(t, text="Move your pointer over the box below to feel the "
                           "current sensitivity and acceleration settings.",
                  wraplength=480, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
        self.test_canvas = tk.Canvas(t, bg=self.COLORS["panel"], highlightthickness=1,
                                      highlightbackground=self.COLORS["muted"])
        self.test_canvas.pack(fill="both", expand=True)
        self.test_dot = None
        self.test_coord_text = self.test_canvas.create_text(
            8, 8, anchor="nw", fill=self.COLORS["muted"],
            font=("TkFixedFont", 9), text="x: -, y: -")
        self.test_canvas.bind("<Motion>", self._on_test_motion)

    def _on_test_motion(self, event):
        r = 7
        if self.test_dot is None:
            self.test_dot = self.test_canvas.create_oval(
                event.x - r, event.y - r, event.x + r, event.y + r,
                fill=self.COLORS["accent"], outline="")
        else:
            self.test_canvas.coords(self.test_dot, event.x - r, event.y - r,
                                     event.x + r, event.y + r)
        self.test_canvas.itemconfigure(
            self.test_coord_text, text="x: %d, y: %d" % (event.x, event.y))

    # ------------------------------------------------------------- log ----
    def _log(self, message, level="info"):
        tag = {"err": "err", "ok": "ok", "warn": "warn"}.get(level, "")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "[%s] %s\n" % (stamp, message), tag)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_controls_enabled(self, enabled):
        state = "normal" if enabled else "disabled"
        for widget in (self.device_combo, self.speed_scale, self.left_handed_check,
                       self.enabled_check, self.natural_check, self.button_scroll_check,
                       self.scroll_button_combo, self.middle_emu_check):
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass
        for rb in getattr(self, "profile_radios", []):
            rb.configure(state=state)

    # --------------------------------------------------------- devices ----
    def refresh_devices(self):
        try:
            self.devices = XInput.list_pointer_devices()
        except XInputError as e:
            self._log("Could not list devices: %s" % e, level="err")
            return
        if not self.devices:
            self._log("No pointing devices found.", level="warn")
            self.device_combo["values"] = []
            return
        labels = ["%s (id %s)" % (name, dev_id) for name, dev_id in self.devices]
        self.device_combo["values"] = labels
        # Try to keep the previously selected device selected, else pick first
        idx = 0
        if self.device_name:
            for i, (name, _id) in enumerate(self.devices):
                if name == self.device_name:
                    idx = i
                    break
        self.device_combo.current(idx)
        self._log("Found %d pointing device(s)." % len(self.devices))
        self.on_device_selected()

    def on_device_selected(self, _event=None):
        idx = self.device_combo.current()
        if idx < 0 or idx >= len(self.devices):
            return
        self.device_name, self.device_id = self.devices[idx]
        try:
            self.props = XInput.get_props(self.device_id)
        except XInputError as e:
            self._log("Could not read properties for '%s': %s" % (self.device_name, e),
                       level="err")
            self.props = {}
        self._log("Selected '%s' (id %s)." % (self.device_name, self.device_id))
        self._sync_controls()

    # ---------------------------------------------------- sync UI<-props --
    def _sync_controls(self):
        self._syncing = True
        try:
            p = self.props

            def first(name, default=None):
                v = p.get(name)
                return v[0] if v else default

            speed = first(PROP_ACCEL_SPEED)
            has_speed = speed is not None
            self.speed_scale.configure(state="normal" if has_speed else "disabled")
            if has_speed:
                try:
                    self.speed_var.set(float(speed))
                except ValueError:
                    self.speed_var.set(0.0)
            self.speed_label.configure(
                text=("%.2f" % self.speed_var.get()) if has_speed else "not supported")

            profile = p.get(PROP_ACCEL_PROFILE)
            has_profile = bool(profile) and len(profile) >= 2
            for rb in self.profile_radios:
                rb.configure(state="normal" if has_profile else "disabled")
            if has_profile:
                self.profile_var.set("adaptive" if profile[0] == "1" else "flat")

            left_handed = first(PROP_LEFT_HANDED)
            has_lh = left_handed is not None
            self.left_handed_check.configure(state="normal" if has_lh else "disabled")
            self.left_handed_var.set(int(left_handed) if has_lh else 0)

            enabled = first(PROP_ENABLED, "1")
            self.enabled_var.set(int(enabled))

            natural = first(PROP_NATURAL_SCROLL)
            has_nat = natural is not None
            self.natural_check.configure(state="normal" if has_nat else "disabled")
            self.natural_var.set(int(natural) if has_nat else 0)

            scroll_method = p.get(PROP_SCROLL_METHOD)
            has_sm = bool(scroll_method) and len(scroll_method) > SCROLL_METHOD_BUTTON_INDEX
            self.button_scroll_check.configure(state="normal" if has_sm else "disabled")
            if has_sm:
                self.button_scroll_var.set(
                    int(scroll_method[SCROLL_METHOD_BUTTON_INDEX]))

            scroll_button = first(PROP_SCROLL_BUTTON)
            has_sb = scroll_button is not None
            self.scroll_button_combo.configure(state="readonly" if has_sb else "disabled")
            if has_sb:
                self.scroll_button_var.set(scroll_button)

            middle_emu = first(PROP_MIDDLE_EMULATION)
            has_me = middle_emu is not None
            self.middle_emu_check.configure(state="normal" if has_me else "disabled")
            self.middle_emu_var.set(int(middle_emu) if has_me else 0)
        finally:
            self._syncing = False

    # -------------------------------------------------------- apply ops ---
    def _apply(self, prop_name, values, description):
        if self._syncing or self.device_id is None:
            return
        try:
            XInput.set_prop(self.device_id, prop_name, values)
            self.props[prop_name] = [str(v) for v in values]
            self._log(description, level="ok")
        except XInputError as e:
            self._log("Failed to set %s: %s" % (prop_name, e), level="err")

    def _on_speed_drag(self, _value):
        self.speed_label.configure(text="%.2f" % self.speed_var.get())

    def _apply_speed(self, _event=None):
        v = round(self.speed_var.get(), 3)
        self._apply(PROP_ACCEL_SPEED, [v], "Accel Speed -> %.2f" % v)

    def _apply_profile(self):
        current = self.props.get(PROP_ACCEL_PROFILE, ["1", "0"])
        n = len(current)
        target_index = 0 if self.profile_var.get() == "adaptive" else 1
        values = [1 if i == target_index else 0 for i in range(n)]
        self._apply(PROP_ACCEL_PROFILE, values,
                    "Accel Profile -> %s" % self.profile_var.get())

    def _apply_left_handed(self):
        v = self.left_handed_var.get()
        self._apply(PROP_LEFT_HANDED, [v],
                    "Left-handed -> %s" % ("on" if v else "off"))

    def _apply_enabled(self):
        v = self.enabled_var.get()
        if v == 0:
            proceed = messagebox.askyesno(
                "Disable device?",
                "This immediately stops '%s' from producing input.\n\n"
                "If it's your only pointing device, use Tab and Space to "
                "re-enable it with the keyboard.\n\nContinue?" % self.device_name)
            if not proceed:
                self._syncing = True
                self.enabled_var.set(1)
                self._syncing = False
                return
        self._apply(PROP_ENABLED, [v], "Device enabled -> %s" % bool(v))

    def _apply_natural(self):
        v = self.natural_var.get()
        self._apply(PROP_NATURAL_SCROLL, [v],
                    "Natural scrolling -> %s" % ("on" if v else "off"))

    def _apply_scroll_method(self):
        current = self.props.get(PROP_SCROLL_METHOD, ["0", "0", "0"])
        values = list(current)
        values[SCROLL_METHOD_BUTTON_INDEX] = 1 if self.button_scroll_var.get() else 0
        self._apply(PROP_SCROLL_METHOD, values,
                    "Click-and-scroll -> %s" % ("on" if self.button_scroll_var.get() else "off"))

    def _apply_scroll_button(self, _event=None):
        v = self.scroll_button_var.get()
        self._apply(PROP_SCROLL_BUTTON, [v], "Scroll button -> %s" % v)

    def _apply_middle_emulation(self):
        v = self.middle_emu_var.get()
        self._apply(PROP_MIDDLE_EMULATION, [v],
                    "Middle-click emulation -> %s" % ("on" if v else "off"))

    # ------------------------------------------------------------- reset --
    def reset_defaults(self):
        if self.device_id is None:
            return
        try:
            fresh = XInput.get_props(self.device_id)
        except XInputError as e:
            self._log("Could not read properties: %s" % e, level="err")
            return
        applied = 0
        for name, values in list(fresh.items()):
            if not name.endswith(" Default"):
                continue
            base_name = name[: -len(" Default")]
            if base_name not in fresh:
                continue
            try:
                XInput.set_prop(self.device_id, base_name, values)
                applied += 1
            except XInputError as e:
                self._log("Failed to reset %s: %s" % (base_name, e), level="err")
        self._log("Reset %d propert(y/ies) to their defaults." % applied, level="ok")
        try:
            self.props = XInput.get_props(self.device_id)
        except XInputError:
            pass
        self._sync_controls()

    # ------------------------------------------------------------ export --
    def export_script(self):
        if self.device_id is None:
            self._log("No device selected.", level="warn")
            return
        try:
            current = XInput.get_props(self.device_id)
        except XInputError as e:
            self._log("Could not read properties: %s" % e, level="err")
            return

        lines = [
            "#!/bin/bash",
            "# Pointer settings for '%s'" % self.device_name,
            "# Generated by Pointer Tuner on %s" % datetime.now().strftime("%Y-%m-%d %H:%M"),
            "#",
            "# Add this script to your session autostart so it re-applies",
            "# these settings automatically at login (e.g. via ~/.xprofile,",
            "# or your desktop environment's Startup Applications tool).",
            "# Only works in X11/XWayland sessions.",
            "",
            'DEVICE_NAME="%s"' % self.device_name,
            'DEVICE_ID=$(xinput list --id-only "$DEVICE_NAME" 2>/dev/null | head -n1)',
            "",
            'if [ -z "$DEVICE_ID" ]; then',
            '    echo "Pointer Tuner: device \'$DEVICE_NAME\' not found, skipping." >&2',
            "    exit 0",
            "fi",
            "",
        ]
        for prop_name in EXPORT_PROPS:
            values = current.get(prop_name)
            if not values:
                continue
            quoted_values = " ".join(values)
            lines.append('xinput set-prop "$DEVICE_ID" "%s" %s' % (prop_name, quoted_values))
        lines.append("")

        default_name = re.sub(r"[^A-Za-z0-9]+", "-", self.device_name).strip("-").lower()
        path = filedialog.asksaveasfilename(
            title="Save startup script",
            defaultextension=".sh",
            initialfile="%s-pointer-settings.sh" % (default_name or "pointer"),
            filetypes=[("Shell script", "*.sh"), ("All files", "*")],
        )
        if not path:
            return
        try:
            with open(path, "w") as f:
                f.write("\n".join(lines))
            os.chmod(path, 0o755)
            self._log("Exported startup script to %s" % path, level="ok")
            messagebox.showinfo(
                "Script exported",
                "Saved to:\n%s\n\nAdd it to your session autostart (e.g. "
                "~/.xprofile) to apply these settings automatically at "
                "every login." % path)
        except OSError as e:
            self._log("Could not write script: %s" % e, level="err")


def main():
    app = PointerTunerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
