import ctypes
import json
import os
import random
import re
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox


# ============================================================
# Windows 11 Realtek MAC Manager
#
# Dynamically queries:
#   Get-NetAdapter
#   Get-NetAdapterAdvancedProperty
#
# No hard-coded registry subkey numbers.
# ============================================================


BACKUP_FILE = os.path.join(
    os.environ.get("PROGRAMDATA", os.getcwd()),
    "RealtekMacManager_backup.json"
)

CLASS_GUID = "{4d36e972-e325-11ce-bfc1-08002be10318}"


# ============================================================
# Administrator
# ============================================================

def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin():
    script = os.path.abspath(sys.argv[0])

    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        f'"{script}"',
        None,
        1
    )

    sys.exit()


# ============================================================
# PowerShell
# ============================================================

def powershell(command):
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            command
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace"
    )

    if result.returncode != 0:
        error = result.stderr.strip()

        raise RuntimeError(
            error or "PowerShell command failed."
        )

    return result.stdout.strip()


def powershell_json(command):
    output = powershell(command)

    if not output:
        return None

    try:
        return json.loads(output)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "Windows returned unexpected PowerShell output:\n\n"
            + output
        ) from e


# ============================================================
# Adapter discovery
# ============================================================

def get_adapters():
    command = r"""
Get-NetAdapter |
Select-Object `
    Name,
    InterfaceDescription,
    InterfaceGuid,
    Status,
    MacAddress,
    ifIndex,
    DriverInformation,
    DriverFileName,
    DriverVersion |
ConvertTo-Json -Compress
"""

    data = powershell_json(command)

    if data is None:
        return []

    if isinstance(data, dict):
        data = [data]

    return data


def get_adapter(name):
    command = f"""
Get-NetAdapter -Name {ps_quote(name)} -ErrorAction Stop |
Select-Object `
    Name,
    InterfaceDescription,
    InterfaceGuid,
    Status,
    MacAddress,
    ifIndex,
    DriverInformation,
    DriverFileName,
    DriverVersion |
ConvertTo-Json -Compress
"""

    data = powershell_json(command)

    if not data:
        raise RuntimeError(
            f"Windows could not find adapter '{name}'."
        )

    return data


# ============================================================
# Advanced properties
# ============================================================

def get_advanced_properties(adapter_name):
    command = f"""
Get-NetAdapterAdvancedProperty `
    -Name {ps_quote(adapter_name)} `
    -AllProperties `
    -ErrorAction Stop |
Select-Object `
    DisplayName,
    DisplayValue,
    RegistryKeyword,
    RegistryValue,
    RegistryDataType |
ConvertTo-Json -Compress
"""

    data = powershell_json(command)

    if data is None:
        return []

    if isinstance(data, dict):
        data = [data]

    return data


def find_network_address_property(properties):
    """
    Find the driver's NetworkAddress property.

    We check RegistryKeyword rather than relying on the
    human-readable DisplayName.
    """

    for prop in properties:

        keyword = prop.get("RegistryKeyword")

        if not keyword:
            continue

        keyword = str(keyword).lower()

        if keyword in (
            "networkaddress",
            "*networkaddress"
        ):
            return prop

    return None


# ============================================================
# PowerShell quoting
# ============================================================

def ps_quote(value):
    """
    Safely quote a string for a PowerShell single-quoted
    string.
    """

    value = str(value).replace("'", "''")

    return "'" + value + "'"


# ============================================================
# MAC handling
# ============================================================

MAC_RE = re.compile(
    r"^[0-9A-Fa-f]{2}([:-][0-9A-Fa-f]{2}){5}$"
)


def normalize_mac(mac):
    mac = mac.strip()
    mac = mac.replace("-", ":")
    return mac.upper()


def valid_mac(mac):
    return bool(MAC_RE.fullmatch(mac.strip()))


def is_unicast_mac(mac):
    first = int(normalize_mac(mac).split(":")[0], 16)

    # Bit 0 = multicast.
    return not bool(first & 1)


def random_mac():
    """
    Generate a locally administered unicast MAC.
    """

    data = [random.randrange(256) for _ in range(6)]

    # Clear multicast bit.
    data[0] &= 0xFE

    # Set locally administered bit.
    data[0] |= 0x02

    return ":".join(f"{x:02X}" for x in data)


# ============================================================
# Backup
# ============================================================

def load_backup():
    if not os.path.exists(BACKUP_FILE):
        return {}

    try:
        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return {}


def save_backup(data):
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def backup_adapter(adapter):
    backups = load_backup()

    name = adapter["Name"]

    if name not in backups:
        backups[name] = {
            "name": name,
            "interface_guid": adapter.get("InterfaceGuid"),
            "description": adapter.get(
                "InterfaceDescription"
            ),
            "original_mac": adapter.get("MacAddress")
        }

        save_backup(backups)


# ============================================================
# Driver capability detection
# ============================================================

def inspect_adapter(adapter):
    properties = get_advanced_properties(
        adapter["Name"]
    )

    network_address = find_network_address_property(
        properties
    )

    return {
        "adapter": adapter,
        "properties": properties,
        "network_address": network_address
    }


# ============================================================
# Apply MAC
# ============================================================

def apply_mac(adapter, mac):
    mac = normalize_mac(mac)

    if not valid_mac(mac):
        raise ValueError(
            "Invalid MAC address.\n\n"
            "Example:\n"
            "02:11:22:33:44:55"
        )

    if not is_unicast_mac(mac):
        raise ValueError(
            "That is a multicast MAC address.\n\n"
            "Use a unicast address."
        )

    info = inspect_adapter(adapter)

    network_address = info["network_address"]

    if not network_address:
        raise RuntimeError(
            "This Windows driver does not expose a "
            "'NetworkAddress' advanced property.\n\n"
            "The program will not guess a registry key."
        )

    backup_adapter(adapter)

    name = adapter["Name"]

    # Use the Windows adapter advanced-property mechanism.
    command = f"""
Set-NetAdapterAdvancedProperty `
    -Name {ps_quote(name)} `
    -RegistryKeyword {ps_quote(network_address["RegistryKeyword"])} `
    -RegistryValue {ps_quote(mac)} `
    -NoRestart `
    -ErrorAction Stop
"""

    powershell(command)

    restart_adapter(name)

    return mac


# ============================================================
# Restart adapter
# ============================================================

def restart_adapter(name):
    command = f"""
Disable-NetAdapter `
    -Name {ps_quote(name)} `
    -Confirm:$false `
    -ErrorAction Stop

Start-Sleep -Milliseconds 1000

Enable-NetAdapter `
    -Name {ps_quote(name)} `
    -Confirm:$false `
    -ErrorAction Stop
"""

    powershell(command)


# ============================================================
# Restore
# ============================================================

def restore_mac(adapter):
    backups = load_backup()

    name = adapter["Name"]

    if name not in backups:
        raise RuntimeError(
            "There is no saved original MAC for this adapter."
        )

    info = inspect_adapter(adapter)

    network_address = info["network_address"]

    if not network_address:
        raise RuntimeError(
            "The Windows driver no longer exposes "
            "the NetworkAddress property."
        )

    keyword = network_address["RegistryKeyword"]

    # Removing the override tells the driver to use
    # its normal/permanent address again.
    command = f"""
Remove-NetAdapterAdvancedProperty `
    -Name {ps_quote(name)} `
    -RegistryKeyword {ps_quote(keyword)} `
    -ErrorAction Stop
"""

    powershell(command)

    restart_adapter(name)

    original = backups[name]["original_mac"]

    del backups[name]
    save_backup(backups)

    return original


# ============================================================
# GUI
# ============================================================

class App:

    def __init__(self, root):
        self.root = root
        self.root.title(
            "Windows 11 Realtek MAC Manager"
        )
        self.root.geometry("760x560")
        self.root.resizable(False, False)

        self.adapters = []

        self.name_var = tk.StringVar()
        self.description_var = tk.StringVar()
        self.guid_var = tk.StringVar()
        self.mac_var = tk.StringVar()
        self.driver_var = tk.StringVar()
        self.status_var = tk.StringVar()
        self.capability_var = tk.StringVar()

        self.build_ui()

        self.refresh()

    # --------------------------------------------------------
    # UI
    # --------------------------------------------------------

    def build_ui(self):

        title = ttk.Label(
            self.root,
            text="Windows 11 MAC Address Manager",
            font=("Segoe UI", 18, "bold")
        )

        title.pack(pady=(18, 4))

        subtitle = ttk.Label(
            self.root,
            text="Dynamic Windows adapter/driver detection"
        )

        subtitle.pack(pady=(0, 18))

        main = ttk.Frame(self.root)
        main.pack(fill="x", padx=30)

        ttk.Label(
            main,
            text="Network adapter:"
        ).pack(anchor="w")

        self.combo = ttk.Combobox(
            main,
            width=85,
            state="readonly"
        )

        self.combo.pack(fill="x", pady=(5, 15))

        self.combo.bind(
            "<<ComboboxSelected>>",
            self.selection_changed
        )

        info = ttk.LabelFrame(
            main,
            text="Adapter information"
        )

        info.pack(fill="x")

        rows = [
            ("Name", self.name_var),
            ("Description", self.description_var),
            ("Interface GUID", self.guid_var),
            ("Current MAC", self.mac_var),
            ("Driver", self.driver_var),
            ("Status", self.status_var),
            ("NetworkAddress", self.capability_var)
        ]

        for row, (label, variable) in enumerate(rows):

            ttk.Label(
                info,
                text=label + ":"
            ).grid(
                row=row,
                column=0,
                sticky="nw",
                padx=10,
                pady=5
            )

            ttk.Label(
                info,
                textvariable=variable,
                wraplength=570
            ).grid(
                row=row,
                column=1,
                sticky="nw",
                padx=10,
                pady=5
            )

        ttk.Label(
            main,
            text="New MAC address:"
        ).pack(
            anchor="w",
            pady=(18, 4)
        )

        mac_frame = ttk.Frame(main)
        mac_frame.pack(fill="x")

        self.mac_entry = ttk.Entry(
            mac_frame,
            width=32
        )

        self.mac_entry.pack(
            side="left"
        )

        ttk.Button(
            mac_frame,
            text="Generate Random",
            command=self.generate
        ).pack(
            side="left",
            padx=8
        )

        button_frame = ttk.Frame(main)
        button_frame.pack(pady=22)

        ttk.Button(
            button_frame,
            text="Apply MAC",
            width=18,
            command=self.apply
        ).grid(
            row=0,
            column=0,
            padx=5
        )

        ttk.Button(
            button_frame,
            text="Restore Original",
            width=18,
            command=self.restore
        ).grid(
            row=0,
            column=1,
            padx=5
        )

        ttk.Button(
            button_frame,
            text="Refresh",
            width=12,
            command=self.refresh
        ).grid(
            row=0,
            column=2,
            padx=5
        )

        self.log = tk.Text(
            main,
            height=8,
            width=85,
            state="disabled"
        )

        self.log.pack(fill="x")

    # --------------------------------------------------------
    # Logging
    # --------------------------------------------------------

    def write_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    # --------------------------------------------------------
    # Refresh
    # --------------------------------------------------------

    def refresh(self):

        try:
            self.adapters = get_adapters()

        except Exception as e:

            messagebox.showerror(
                "Adapter detection failed",
                str(e)
            )

            return

        values = [
            f"{a.get('Name', '?')} — "
            f"{a.get('InterfaceDescription', '?')}"
            for a in self.adapters
        ]

        self.combo["values"] = values

        if self.adapters:

            self.combo.current(0)

            self.selection_changed()

            self.write_log(
                f"Detected {len(self.adapters)} adapter(s)."
            )

    # --------------------------------------------------------
    # Selection
    # --------------------------------------------------------

    def selected(self):

        index = self.combo.current()

        if index < 0:
            return None

        if index >= len(self.adapters):
            return None

        return self.adapters[index]

    def selection_changed(self, event=None):

        adapter = self.selected()

        if not adapter:
            return

        self.name_var.set(
            adapter.get("Name", "")
        )

        self.description_var.set(
            adapter.get(
                "InterfaceDescription",
                ""
            )
        )

        self.guid_var.set(
            str(adapter.get("InterfaceGuid", ""))
        )

        self.mac_var.set(
            adapter.get("MacAddress", "")
        )

        driver = adapter.get(
            "DriverInformation"
        ) or "Unknown"

        self.driver_var.set(
            str(driver)
        )

        self.status_var.set(
            adapter.get("Status", "")
        )

        try:
            properties = get_advanced_properties(
                adapter["Name"]
            )

            prop = find_network_address_property(
                properties
            )

            if prop:

                self.capability_var.set(
                    "FOUND — "
                    + str(
                        prop.get(
                            "RegistryKeyword"
                        )
                    )
                )

            else:

                self.capability_var.set(
                    "NOT EXPOSED BY DRIVER"
                )

        except Exception as e:

            self.capability_var.set(
                "Detection error: " + str(e)
            )

    # --------------------------------------------------------
    # Random
    # --------------------------------------------------------

    def generate(self):

        self.mac_entry.delete(0, tk.END)

        self.mac_entry.insert(
            0,
            random_mac()
        )

    # --------------------------------------------------------
    # Apply
    # --------------------------------------------------------

    def apply(self):

        adapter = self.selected()

        if not adapter:
            messagebox.showwarning(
                "No adapter",
                "Select an adapter first."
            )

            return

        mac = self.mac_entry.get().strip()

        if not valid_mac(mac):

            messagebox.showerror(
                "Invalid MAC",
                "Use a format such as:\n\n"
                "02:11:22:33:44:55"
            )

            return

        try:

            new_mac = apply_mac(
                adapter,
                mac
            )

            self.write_log(
                f"Applied requested MAC {new_mac} "
                f"to {adapter['Name']}."
            )

            messagebox.showinfo(
                "Completed",
                "The adapter was configured and restarted.\n\n"
                f"Requested MAC:\n{new_mac}\n\n"
                "The displayed MAC will be refreshed."
            )

            self.refresh()

        except Exception as e:

            self.write_log(
                "ERROR: " + str(e)
            )

            messagebox.showerror(
                "MAC change failed",
                str(e)
            )

    # --------------------------------------------------------
    # Restore
    # --------------------------------------------------------

    def restore(self):

        adapter = self.selected()

        if not adapter:
            return

        answer = messagebox.askyesno(
            "Restore original MAC",
            "Restore the original MAC for:\n\n"
            + adapter["Name"]
        )

        if not answer:
            return

        try:

            original = restore_mac(
                adapter
            )

            self.write_log(
                f"Restored {adapter['Name']} "
                f"to {original}."
            )

            messagebox.showinfo(
                "Restored",
                f"Original MAC restored:\n\n{original}"
            )

            self.refresh()

        except Exception as e:

            self.write_log(
                "ERROR: " + str(e)
            )

            messagebox.showerror(
                "Restore failed",
                str(e)
            )


# ============================================================
# Main
# ============================================================

if __name__ == "__main__":

    if os.name != "nt":

        messagebox.showerror(
            "Windows only",
            "This program is designed for Windows 11."
        )

        sys.exit(1)

    if not is_admin():

        relaunch_as_admin()

    root = tk.Tk()

    app = App(root)

    root.mainloop()

