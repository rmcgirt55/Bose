from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import subprocess
import threading
import json
import re
from urllib.parse import urlparse, parse_qs

PREVIEW = Path(__file__).resolve().parent
REVIVAL = Path(__file__).resolve().parent.parent / "BluetoothController"
PYTHON = Path(__import__("sys").executable)

BLUETOOTH_LOCK = threading.Lock()

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PREVIEW), **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/status":
            return self.handle_status(parsed.query)

        if parsed.path != "/api/scan":
            return super().do_GET()

        try:
            # Save known addresses BEFORE cli.py scan overwrites them.
            address_file = REVIVAL / ".sleepbuds_addresses"
            previous_addresses = {}
            saved = {}

            if address_file.exists():
                for line in address_file.read_text().splitlines():
                    if "=" in line:
                        name, address = line.split("=", 1)
                        previous_addresses[name.strip()] = address.strip()

            result = subprocess.run(
                [str(PYTHON), "cli.py", "scan"],
                cwd=str(REVIVAL),
                capture_output=True,
                text=True,
                timeout=35
            )

            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip() or
                    result.stdout.strip() or
                    "Bluetooth scan failed"
                )

            devices = []
            pattern = re.compile(
                r"\[\d+\]\s+(.+?)\s*\n\s*Address:\s*(\S+)"
            )

            for name, address in pattern.findall(result.stdout):
                devices.append({
                    "name": name.strip(),
                    "address": address
                })

            # Preserve previously verified addresses if a scan
            # temporarily discovers only one Sleepbud.
            address_file = REVIVAL / ".sleepbuds_addresses"

            if previous_addresses or address_file.exists():
                saved = previous_addresses.copy()

                for line in address_file.read_text().splitlines():
                    if "=" in line:
                        name, address = line.split("=", 1)
                        saved[name.strip()] = address.strip()

                for device in devices:
                    saved[device["name"]] = device["address"]

                address_file.write_text(
                    "\n".join(
                        f"{name}={address}"
                        for name, address in saved.items()
                    )
                )

            # Include previously saved earbuds in the browser results.
            # A saved address does not mean the earbud is connected.
            discovered_addresses = {
                device["address"] for device in devices
            }

            for name, address in saved.items():
                if address not in discovered_addresses:
                    devices.append({
                        "name": name,
                        "address": address,
                        "discovered": False
                    })

            for device in devices:
                device.setdefault("discovered", True)

            self.respond({
                "success": True,
                "devices": devices
            })

        except Exception as error:
            self.respond({
                "success": False,
                "error": str(error)
            }, 500)

    def handle_status(self, query):
        params = parse_qs(query)
        address = params.get("address", [""])[0]

        if not re.fullmatch(r"[A-Fa-f0-9-]{36}", address):
            return self.respond({
                "success": False,
                "error": "Invalid Bluetooth address"
            }, 400)

        try:
            result = subprocess.run(
                [
                    str(PYTHON),
                    "cli.py",
                    "status",
                    "--address",
                    address,
                    "--timeout",
                    "15"
                ],
                cwd=str(REVIVAL),
                capture_output=True,
                text=True,
                timeout=45
            )

            output = result.stdout

            if "Bose Sleepbuds II Status" not in output:
                raise RuntimeError(
                    result.stderr.strip() or
                    output.strip() or
                    "Unable to connect"
                )

            def field(name):
                match = re.search(
                    rf"^\s*{re.escape(name)}:\s*(.*?)\s*$",
                    output,
                    re.MULTILINE
                )
                return match.group(1) if match else "N/A"

            self.respond({
                "success": True,
                "address": address,
                "name": field("Name"),
                "firmware": field("Firmware"),
                "battery": field("Battery"),
                "phone_free": field("Phone-free mode"),
                "playing": field("Playing"),
                "sound": field("Sound"),
                "volume": field("Volume")
            })

        except Exception as error:
            self.respond({
                "success": False,
                "error": str(error)
            }, 500)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path not in ("/api/play", "/api/stop"):
            return self.respond({
                "success": False,
                "error": "Unknown endpoint"
            }, 404)

        try:
            length = int(self.headers.get("Content-Length", "0"))

            if length > 4096:
                raise ValueError("Request too large")

            payload = json.loads(
                self.rfile.read(length) or b"{}"
            )

            if parsed.path == "/api/play":
                sound = int(payload.get("sound", 35))
                volume = int(payload.get("volume", 35))
                timer = int(payload.get("timer", 0))

                allowed_sounds = {
                    35, 31, 33, 49, 55, 57,
                    76, 81, 93, 94, 95, 103
                }

                if sound not in allowed_sounds:
                    raise ValueError("Invalid sound")

                if not 0 <= volume <= 255:
                    raise ValueError("Invalid volume")

                if timer not in (0, 900, 1800, 3600):
                    raise ValueError("Invalid sleep timer")

                command = [
                    str(PYTHON), "cli.py", "play",
                    str(sound),
                    "--both",
                    "--volume", str(volume),
                    "--sleep-timer", str(timer),
                    "--timeout", "45"
                ]

            else:
                command = [
                    str(PYTHON), "safe_stop.py"
                ]

            addresses = REVIVAL / ".sleepbuds_addresses"

            if not addresses.exists():
                raise RuntimeError("No saved Sleepbud addresses")

            saved = addresses.read_text()

            if not (
                "Bose Sleepbuds L=" in saved
                and "Bose Sleepbuds R=" in saved
            ):
                raise RuntimeError(
                    "Both Sleepbuds must be saved before playback"
                )

            result = subprocess.run(
                command,
                cwd=str(REVIVAL),
                capture_output=True,
                text=True,
                timeout=120
            )

            output = result.stdout.strip()

            if result.returncode != 0:
                raise RuntimeError(
                    result.stderr.strip() or output
                    or "Bluetooth command failed"
                )

            if "Need both bud addresses" in output:
                raise RuntimeError(output)

            if parsed.path == "/api/play":
                expected = ("Bose Sleepbuds L", "Bose Sleepbuds R")

                for name in expected:
                    match = re.search(
                        rf"^\s*{re.escape(name)}: playing=(True|False)",
                        output,
                        re.MULTILINE
                    )

                    if not match or match.group(1) != "True":
                        raise RuntimeError(
                            f"Playback not confirmed for {name}. "
                            f"Controller output:\n{output}"
                        )

            self.respond({
                "success": True,
                "output": output
            })

        except Exception as error:
            self.respond({
                "success": False,
                "error": str(error)
            }, 500)

    def respond(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# Serialize Bluetooth operations across browser requests.
_original_do_GET = Handler.do_GET
_original_do_POST = Handler.do_POST

def locked_get(self):
    path = urlparse(self.path).path
    if path in ("/api/scan", "/api/status"):
        with BLUETOOTH_LOCK:
            return _original_do_GET(self)
    return _original_do_GET(self)

def locked_post(self):
    path = urlparse(self.path).path
    if path in ("/api/play", "/api/stop"):
        with BLUETOOTH_LOCK:
            return _original_do_POST(self)
    return _original_do_POST(self)

Handler.do_GET = locked_get
Handler.do_POST = locked_post

if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Sleepbuds browser preview running")
    print("Open: http://127.0.0.1:8765")
    server.serve_forever()
