"""Drivers de controle remoto para várias marcas de TV, emissores IR e descoberta na rede."""
import asyncio
import base64
import collections
import json
import socket
import struct
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)

LOG = collections.deque(maxlen=300)


def log(msg):
    LOG.append(f"{time.strftime('%H:%M:%S')}  {msg}")


KEYS = [
    "power", "up", "down", "left", "right", "ok", "back", "home", "menu",
    "vol_up", "vol_down", "mute", "ch_up", "ch_down",
    "play", "pause", "rewind", "forward",
    "source", "info", "exit",
] + [f"num_{i}" for i in range(10)]

# Apps mais comuns (nome mostrado na interface)
POPULAR = ["Netflix", "YouTube", "Prime Video", "Disney+", "Max", "Globoplay", "Spotify", "Apple TV"]


class DriverError(Exception):
    pass


class Driver:
    HINT = ""
    needs_pairing = False

    def __init__(self, dev):
        self.dev = dev
        self.host = dev.get("host", "")
        self.id = dev["id"]

    # --- ciclo de vida
    def close(self):
        pass

    def reset(self):
        """Esquece pareamento/credenciais para refazer a conexão do zero."""
        self.close()
        DATA.mkdir(parents=True, exist_ok=True)
        for f in DATA.glob(f"*_{self.id}*"):
            try:
                f.unlink()
            except OSError:
                pass

    def _retry(self, fn, what):
        last = None
        for _ in range(2):
            try:
                return fn()
            except DriverError:
                raise
            except Exception as e:  # noqa: BLE001 - bibliotecas lançam vários tipos
                last = e
                log(f"{what}: {type(e).__name__}: {e}")
                self.close()
        raise DriverError(f"{what} não respondeu ({type(last).__name__}: {last}). {self.HINT}")

    # --- API
    def send(self, key):
        raise NotImplementedError

    def apps(self):
        return []

    def launch(self, app_id):
        raise DriverError("Esta TV não permite abrir apps pelo controle.")

    def type(self, delta, full):
        raise DriverError("Esta TV não aceita digitação pelo controle.")

    def erase(self, n, full):
        raise DriverError("Esta TV não aceita digitação pelo controle.")

    def enter(self):
        self.send("ok")

    def test(self):
        raise NotImplementedError

    def pair_start(self):
        raise DriverError("Esta TV não precisa de código de pareamento.")

    def pair_finish(self, code):
        raise DriverError("Esta TV não precisa de código de pareamento.")

    def unsupported(self, key):
        raise DriverError(f"Esta TV não tem a tecla '{key}'.")


# --------------------------------------------------------------------------- Roku
class Roku(Driver):
    HINT = "Ative 'Controle por apps móveis' em Configurações > Sistema > Configurações avançadas > Controle por apps móveis (Permissivo)."
    MAP = {
        "power": "Power", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "ok": "Select", "back": "Back", "home": "Home", "menu": "Info", "info": "Info",
        "vol_up": "VolumeUp", "vol_down": "VolumeDown", "mute": "VolumeMute",
        "ch_up": "ChannelUp", "ch_down": "ChannelDown",
        "play": "Play", "pause": "Play", "rewind": "Rev", "forward": "Fwd", "exit": "Back",
    }

    def _url(self, path):
        return f"http://{self.host}:8060{path}"

    def _post(self, path):
        try:
            requests.post(self._url(path), timeout=3).raise_for_status()
        except requests.RequestException as e:
            raise DriverError(f"Roku não respondeu: {e}. {self.HINT}")

    def send(self, key):
        if key.startswith("num_"):
            return self._post(f"/keypress/Lit_{key[4:]}")
        if key not in self.MAP:
            self.unsupported(key)
        self._post(f"/keypress/{self.MAP[key]}")

    def apps(self):
        try:
            r = requests.get(self._url("/query/apps"), timeout=4)
            return [{"id": a.get("id"), "name": a.text} for a in ET.fromstring(r.text)]
        except (requests.RequestException, ET.ParseError) as e:
            raise DriverError(f"Não consegui listar apps: {e}")

    def launch(self, app_id):
        self._post(f"/launch/{urllib.parse.quote(str(app_id))}")

    def type(self, delta, full):
        for ch in delta:
            self._post(f"/keypress/Lit_{urllib.parse.quote(ch, safe='')}")

    def erase(self, n, full):
        for _ in range(n):
            self._post("/keypress/Backspace")

    def enter(self):
        self._post("/keypress/Enter")

    def test(self):
        try:
            x = ET.fromstring(requests.get(self._url("/query/device-info"), timeout=4).text)
            name = (x.findtext("user-device-name") or x.findtext("friendly-device-name")
                    or x.findtext("model-name") or "Roku")
            return {"name": name, "info": x.findtext("model-name") or ""}
        except (requests.RequestException, ET.ParseError) as e:
            raise DriverError(f"Roku não respondeu: {e}. {self.HINT}")


# ------------------------------------------------------------------------ Samsung
class Samsung(Driver):
    HINT = ("Ligue a TV, deixe no mesmo Wi-Fi e, na primeira vez, aceite o pedido 'Permitir' na tela da TV. "
            "Se negou sem querer: Configurações > Geral > Gerenciador de dispositivos externos > Lista de dispositivos.")
    MAP = {
        "power": "KEY_POWER", "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT",
        "right": "KEY_RIGHT", "ok": "KEY_ENTER", "back": "KEY_RETURN", "home": "KEY_HOME",
        "menu": "KEY_MENU", "vol_up": "KEY_VOLUP", "vol_down": "KEY_VOLDOWN",
        "mute": "KEY_MUTE", "ch_up": "KEY_CHUP", "ch_down": "KEY_CHDOWN",
        "play": "KEY_PLAY", "pause": "KEY_PAUSE", "rewind": "KEY_REWIND", "forward": "KEY_FF",
        "source": "KEY_SOURCE", "info": "KEY_INFO", "exit": "KEY_EXIT",
        **{f"num_{i}": f"KEY_{i}" for i in range(10)},
    }
    APPS = [
        ("Netflix", "11101200001"), ("YouTube", "111299001912"), ("Prime Video", "3201512006785"),
        ("Disney+", "3201901017640"), ("Spotify", "3201606009684"), ("Apple TV", "3201807016597"),
    ]

    def __init__(self, dev):
        super().__init__(dev)
        self.tv = None
        self.lock = threading.Lock()

    def _tv(self):
        if self.tv is None:
            try:
                from samsungtvws import SamsungTVWS
            except ImportError:
                raise DriverError("Falta instalar: pip install samsungtvws")
            self.tv = SamsungTVWS(
                host=self.host, port=8002, token_file=str(DATA / f"samsung_{self.id}.token"),
                name="Controle Universal", timeout=10, key_press_delay=0,
            )
        return self.tv

    def close(self):
        try:
            if self.tv:
                self.tv.close()
        except Exception:  # noqa: BLE001
            pass
        self.tv = None

    def _do(self, fn, what="Samsung"):
        with self.lock:
            return self._retry(lambda: fn(self._tv()), what)

    def send(self, key):
        if key not in self.MAP:
            self.unsupported(key)
        self._do(lambda tv: tv.send_key(self.MAP[key]))

    def apps(self):
        def get(tv):
            try:
                return tv.app_list() or []
            except Exception:  # noqa: BLE001
                return []
        listed = self._do(get)
        if listed:
            return [{"id": a.get("appId"), "name": a.get("name")} for a in listed if a.get("appId")]
        return [{"id": i, "name": n} for n, i in self.APPS]

    def launch(self, app_id):
        self._do(lambda tv: tv.run_app(str(app_id)))

    def type(self, delta, full):
        self._do(lambda tv: tv.send_text(full))

    def erase(self, n, full):
        if full:
            self._do(lambda tv: tv.send_text(full))

    def enter(self):
        self._do(lambda tv: tv.end_text())

    def test(self):
        def info(tv):
            d = tv.rest_device_info().get("device", {})
            return {"name": d.get("name", "Samsung"), "info": d.get("modelName", "")}
        return self._do(info)


# ---------------------------------------------------------------------------- LG
class LG(Driver):
    HINT = ("Na TV: Configurações > Geral > Rede > 'LG Connect Apps' ligado, e aceite o pedido na tela na primeira vez. "
            "Se a TV estava desligada, ligue-a primeiro.")
    INP = {
        "up": "up", "down": "down", "left": "left", "right": "right", "ok": "ok", "back": "back",
        "home": "home", "menu": "menu", "info": "info", "exit": "exit",
        "vol_up": "volume_up", "vol_down": "volume_down", "mute": "mute",
        "ch_up": "channel_up", "ch_down": "channel_down",
        "play": "play", "pause": "pause", "rewind": "rewind", "forward": "fastforward",
        **{f"num_{i}": f"num_{i}" for i in range(10)},
    }
    APPS = [
        ("Netflix", "netflix"), ("YouTube", "youtube.leanback.v4"), ("Prime Video", "amazon"),
        ("Disney+", "com.disney.disneyplus-prod"), ("Spotify", "spotify-beehive"),
    ]

    def __init__(self, dev):
        super().__init__(dev)
        self.c = None
        self.lock = threading.Lock()

    def _store(self):
        return DATA / f"lg_{self.id}.json"

    def _connect(self):
        try:
            from pywebostv.connection import WebOSClient
            from pywebostv.controls import ApplicationControl, InputControl, SystemControl
        except ImportError:
            raise DriverError("Falta instalar: pip install pywebostv")
        store = json.loads(self._store().read_text()) if self._store().exists() else {}
        client, err = None, None
        for secure in (False, True):  # webOS antigo usa 3000; novo usa 3001 (wss)
            try:
                cl = WebOSClient(self.host, secure=secure)
                cl.connect()
                client = cl
                break
            except Exception as e:  # noqa: BLE001
                err = e
        if client is None:
            raise DriverError(f"LG não respondeu ({err}). {self.HINT}")
        for _ in client.register(store, timeout=60):
            pass  # aguarda o usuário aceitar na TV
        self._store().write_text(json.dumps(store))
        self.c = {
            "client": client, "input": InputControl(client),
            "system": SystemControl(client), "apps": ApplicationControl(client),
        }
        self.c["input"].connect_input()

    def close(self):
        try:
            if self.c:
                self.c["client"].close()
        except Exception:  # noqa: BLE001
            pass
        self.c = None

    def _do(self, fn, what="LG"):
        with self.lock:
            def run():
                if self.c is None:
                    self._connect()
                return fn(self.c)
            return self._retry(run, what)

    def send(self, key):
        if key == "power":
            return self._do(lambda c: c["system"].power_off())
        if key not in self.INP:
            self.unsupported(key)
        self._do(lambda c: getattr(c["input"], self.INP[key])())

    def apps(self):
        def get(c):
            out = []
            for a in c["apps"].list_apps():
                try:
                    if a["visible"] is False:
                        continue
                    out.append({"id": a["id"], "name": a["title"]})
                except KeyError:
                    out.append({"id": a["id"], "name": a["id"]})
            return out
        try:
            return self._do(get) or [{"id": i, "name": n} for n, i in self.APPS]
        except DriverError:
            return [{"id": i, "name": n} for n, i in self.APPS]

    def launch(self, app_id):
        from pywebostv.model import Application
        self._do(lambda c: c["apps"].launch(Application({"id": app_id, "title": app_id})))

    def type(self, delta, full):
        self._do(lambda c: c["input"].type(delta))

    def erase(self, n, full):
        self._do(lambda c: c["input"].delete(n))

    def enter(self):
        self._do(lambda c: c["input"].enter())

    def test(self):
        info = self._do(lambda c: c["system"].info())
        return {"name": "LG webOS", "info": str(info.get("product_name", "") if isinstance(info, dict) else info)}


# -------------------------------------------------------------------------- Sony
class Sony(Driver):
    HINT = ("Na Sony: Configurações > Rede > Configuração de rede doméstica > Controle IP > "
            "Autenticação 'Normal e Pre-Shared Key' e defina um 'Pre-Shared Key' (ex.: 0000); informe o mesmo aqui.")
    NAMES = {
        "power": "PowerOff", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "ok": "Confirm", "back": "Return", "home": "Home", "menu": "Options", "info": "Display",
        "vol_up": "VolumeUp", "vol_down": "VolumeDown", "mute": "Mute",
        "ch_up": "ChannelUp", "ch_down": "ChannelDown",
        "play": "Play", "pause": "Pause", "rewind": "Rewind", "forward": "Forward",
        "source": "Input", "exit": "Exit",
        **{f"num_{i}": f"Num{i}" for i in range(10)},
    }

    def __init__(self, dev):
        super().__init__(dev)
        self.codes = None

    def close(self):
        self.codes = None

    def _headers(self):
        return {"X-Auth-PSK": self.dev["psk"]} if self.dev.get("psk") else {}

    def _rpc(self, service, method, params=None, version="1.0"):
        try:
            r = requests.post(
                f"http://{self.host}/sony/{service}",
                json={"method": method, "id": 1, "params": params or [], "version": version},
                headers=self._headers(), timeout=4,
            )
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise DriverError(f"Sony não respondeu: {e}. {self.HINT}")
        if "error" in data:
            raise DriverError(f"Sony recusou ({data['error']}). {self.HINT}")
        return data.get("result", [])

    def _load_codes(self):
        if self.codes is None:
            res = self._rpc("system", "getRemoteControllerInfo")
            self.codes = {c["name"]: c["value"] for c in res[1]}
        return self.codes

    def send(self, key):
        if key == "power":
            status = self._rpc("system", "getPowerStatus")[0].get("status")
            if status == "standby":
                self._rpc("system", "setPowerStatus", [{"status": True}])
                return
        name = self.NAMES.get(key)
        if not name:
            self.unsupported(key)
        codes = self._load_codes()
        if name not in codes:
            self.unsupported(key)
        body = (
            '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
            's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
            '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
            f"<IRCCCode>{codes[name]}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>"
        )
        headers = {
            "Content-Type": "text/xml; charset=UTF-8",
            "SOAPACTION": '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"', **self._headers(),
        }
        try:
            requests.post(f"http://{self.host}/sony/IRCC", data=body, headers=headers, timeout=4).raise_for_status()
        except requests.RequestException as e:
            raise DriverError(f"Sony recusou o comando: {e}. {self.HINT}")

    def apps(self):
        res = self._rpc("appControl", "getApplicationList")
        return [{"id": a["uri"], "name": a["title"]} for a in (res[0] if res else [])]

    def launch(self, app_id):
        self._rpc("appControl", "setActiveApp", [{"uri": app_id}])

    def type(self, delta, full):
        self._rpc("appControl", "setTextForm", [full])

    def erase(self, n, full):
        self._rpc("appControl", "setTextForm", [full])

    def test(self):
        info = self._rpc("system", "getSystemInformation")
        d = info[0] if info else {}
        return {"name": d.get("model", "Sony Bravia"), "info": d.get("product", "")}


# --------------------------------------------------------- Android TV / Google TV
class _Loop:
    loop = None

    @classmethod
    def run(cls, coro, timeout=25):
        if cls.loop is None:
            cls.loop = asyncio.new_event_loop()
            threading.Thread(target=cls.loop.run_forever, daemon=True).start()
        return asyncio.run_coroutine_threadsafe(coro, cls.loop).result(timeout)


class AndroidTV(Driver):
    needs_pairing = True
    HINT = "Se for a primeira vez ou após resetar, use 'Parear' e digite o código que aparece na TV. Ligue a TV antes."
    MAP = {
        "power": "POWER", "up": "DPAD_UP", "down": "DPAD_DOWN", "left": "DPAD_LEFT",
        "right": "DPAD_RIGHT", "ok": "DPAD_CENTER", "back": "BACK", "home": "HOME",
        "menu": "MENU", "info": "INFO", "exit": "BACK", "source": "TV_INPUT",
        "vol_up": "VOLUME_UP", "vol_down": "VOLUME_DOWN", "mute": "VOLUME_MUTE",
        "ch_up": "CHANNEL_UP", "ch_down": "CHANNEL_DOWN",
        "play": "MEDIA_PLAY", "pause": "MEDIA_PAUSE",
        "rewind": "MEDIA_REWIND", "forward": "MEDIA_FAST_FORWARD",
        **{f"num_{i}": str(i) for i in range(10)},
    }
    APPS = [
        ("Netflix", "https://www.netflix.com/title"), ("YouTube", "https://www.youtube.com"),
        ("Prime Video", "https://app.primevideo.com"), ("Disney+", "https://www.disneyplus.com"),
        ("Spotify", "spotify://"), ("Google Play Store", "market://launch?id=com.android.vending"),
    ]

    def __init__(self, dev):
        super().__init__(dev)
        self.remote = None
        self.connected = False

    def _remote(self):
        if self.remote is None:
            try:
                from androidtvremote2 import AndroidTVRemote
            except ImportError:
                raise DriverError("Falta instalar: pip install androidtvremote2")
            self.remote = AndroidTVRemote(
                "Controle Universal", str(DATA / f"atv_{self.id}_cert.pem"),
                str(DATA / f"atv_{self.id}_key.pem"), self.host,
            )
        return self.remote

    def close(self):
        r, self.remote, self.connected = self.remote, None, False
        if r:
            try:
                r.disconnect()
            except Exception:  # noqa: BLE001
                pass

    async def _ensure(self):
        from androidtvremote2 import InvalidAuth
        r = self._remote()
        await r.async_generate_cert_if_missing()
        if not self.connected:
            try:
                await r.async_connect()
            except InvalidAuth:
                raise DriverError("Esta TV ainda não está pareada. Toque em 'Parear' e digite o código da TV.")
            self.connected = True
        return r

    def _do(self, fn, what="Android TV"):
        async def run():
            return fn(await self._ensure())
        return self._retry(lambda: _Loop.run(run()), what)

    def send(self, key):
        if key not in self.MAP:
            self.unsupported(key)
        self._do(lambda r: r.send_key_command(self.MAP[key]))

    def apps(self):
        return [{"id": i, "name": n} for n, i in self.APPS]

    def launch(self, app_id):
        self._do(lambda r: r.send_launch_app_command(app_id))

    def type(self, delta, full):
        self._do(lambda r: r.send_text(delta))

    def erase(self, n, full):
        def go(r):
            for _ in range(n):
                r.send_key_command("DEL")
        self._do(go)

    def enter(self):
        self._do(lambda r: r.send_key_command("ENTER"))

    def test(self):
        def info(r):
            d = r.device_info or {}
            return {"name": d.get("model", "Android TV"), "info": d.get("manufacturer", "")}
        return self._do(info)

    def pair_start(self):
        self.close()

        async def go():
            r = self._remote()
            await r.async_generate_cert_if_missing()
            await r.async_start_pairing()
        try:
            _Loop.run(go())
        except Exception as e:  # noqa: BLE001
            self.close()
            raise DriverError(f"Não consegui iniciar o pareamento ({e}). Ligue a TV e confira o IP.")

    def pair_finish(self, code):
        try:
            _Loop.run(self._remote().async_finish_pairing(code.strip()))
        except Exception as e:  # noqa: BLE001
            raise DriverError(f"Código recusado ({e}). Toque em 'Iniciar' para gerar outro código.")
        self.connected = False


# ------------------------------------------------------- Infravermelho (qualquer TV)
def _sony_code(cmd, addr=1):
    bits = [(cmd >> i) & 1 for i in range(7)] + [(addr >> i) & 1 for i in range(5)]
    n = 0
    for b in bits:
        n = (n << 1) | b
    return n


_LG_DIG = [0x08F7, 0x8877, 0x48B7, 0xC837, 0x28D7, 0xA857, 0x6897, 0xE817, 0x18E7, 0x9867]
_SS_DIG = [0x8877, 0x20DF, 0xA05F, 0x609F, 0x10EF, 0x906F, 0x50AF, 0x30CF, 0xB04F, 0x708F]

PRESETS = {
    "lg": ("NEC", 32, {
        "power": 0x20DF10EF, "vol_up": 0x20DF40BF, "vol_down": 0x20DFC03F, "mute": 0x20DF906F,
        "up": 0x20DF02FD, "down": 0x20DF827D, "left": 0x20DFE01F, "right": 0x20DF609F,
        "ok": 0x20DF22DD, "back": 0x20DF14EB, "home": 0x20DF3EC1, "menu": 0x20DFC23D,
        "ch_up": 0x20DF00FF, "ch_down": 0x20DF807F, "source": 0x20DFD02F,
        "info": 0x20DF55AA, "exit": 0x20DFDA25,
        **{f"num_{i}": 0x20DF0000 | _LG_DIG[i] for i in range(10)},
    }),
    "samsung": ("SAMSUNG", 32, {
        "power": 0xE0E040BF, "vol_up": 0xE0E0E01F, "vol_down": 0xE0E0D02F, "mute": 0xE0E0F00F,
        "up": 0xE0E006F9, "down": 0xE0E08679, "left": 0xE0E0A659, "right": 0xE0E046B9,
        "ok": 0xE0E016E9, "back": 0xE0E01AE5, "home": 0xE0E09E61, "menu": 0xE0E058A7,
        "ch_up": 0xE0E048B7, "ch_down": 0xE0E008F7, "source": 0xE0E0807F,
        "info": 0xE0E0F807, "exit": 0xE0E0B44B,
        **{f"num_{i}": 0xE0E00000 | _SS_DIG[i] for i in range(10)},
    }),
    "sony": ("SONY", 12, {
        "power": _sony_code(0x15), "vol_up": _sony_code(0x12), "vol_down": _sony_code(0x13),
        "mute": _sony_code(0x14), "ch_up": _sony_code(0x00), "ch_down": _sony_code(0x01),
        "up": _sony_code(0x74), "down": _sony_code(0x75), "left": _sony_code(0x34),
        "right": _sony_code(0x33), "ok": _sony_code(0x65), "home": _sony_code(0x60),
        "source": _sony_code(0x25), "info": _sony_code(0x3A),
        **{f"num_{(i + 1) % 10}": _sony_code(i) for i in range(10)},
    }),
}
PRESET_NAMES = {"lg": "LG", "samsung": "Samsung", "sony": "Sony"}


def _nec_like_pulses(proto, code):
    head = (9000, 4500) if proto == "NEC" else (4500, 4500)
    out = list(head)
    for byte in code.to_bytes(4, "big"):
        for i in range(8):  # cada byte sai com o bit menos significativo primeiro
            out += [560, 1690 if (byte >> i) & 1 else 560]
    out += [560, 40000]
    return out


def _sony_pulses(code, bits=12, frames=3):
    out = []
    for _ in range(frames):
        frame = [2400, 600]
        for i in range(bits - 1, -1, -1):
            frame += [1200 if (code >> i) & 1 else 600, 600]
        out += frame[:-1] + [max(45000 - sum(frame) + 600, 600)]
    return out


def broadlink_packet(proto, bits, code):
    pulses = _sony_pulses(code, bits) if proto == "SONY" else _nec_like_pulses(proto, code)
    res = bytearray([0x26, 0x00, 0x00, 0x00])
    for d in pulses:
        n = int(round(d * 269 / 8192))
        res += bytes([0, n >> 8, n & 0xFF]) if n > 255 else bytes([n])
    size = len(res) - 4
    res[2], res[3] = size & 0xFF, size >> 8
    res += b"\x0d\x05"
    rem = (len(res) + 4) % 16
    if rem:
        res += bytes(16 - rem)
    return bytes(res)


class _IR(Driver):
    def lookup(self, key):
        """Retorna ('learned', bytes) | ('proto', (proto, bits, code))."""
        custom = (self.dev.get("ir_codes") or {}).get(key)
        if custom:
            if custom.startswith("bl:"):
                return "learned", base64.b64decode(custom[3:])
            proto, _, data = custom.partition(":")
            return "proto", (proto.upper(), 32 if proto.upper() != "SONY" else 12, int(data, 16))
        preset = self.dev.get("ir_preset", "lg")
        proto, bits, table = PRESETS.get(preset, PRESETS["lg"])
        if key in table:
            return "proto", (proto, bits, table[key])
        raise DriverError(
            f"Sem código IR para '{key}' ({PRESET_NAMES.get(preset, preset)}). "
            "Aprenda esta tecla em Gerenciar > Aprender tecla (precisa de emissor Broadlink)."
        )

    def apps(self):
        raise DriverError("TV por infravermelho não abre apps diretamente. Use o botão Home e as setas.")

    def test(self):
        self.send("info")
        return {"name": "Emissor IR", "info": "enviou a tecla INFO; veja se a TV reagiu"}


class IRTasmota(_IR):
    HINT = "Confira o IP do ESP8266/ESP32 com Tasmota (abra http://IP no navegador) e se o LED IR está no pino configurado."

    def send(self, key):
        kind, val = self.lookup(key)
        if kind == "learned":
            raise DriverError("Códigos aprendidos só funcionam com emissor Broadlink.")
        proto, bits, code = val
        payload = {"Protocol": proto, "Bits": bits, "Data": f"0x{code:X}"}
        if proto == "SONY":
            payload["Repeat"] = 2
        try:
            requests.get(f"http://{self.host}/cm", params={"cmnd": "IRsend " + json.dumps(payload)},
                         timeout=3).raise_for_status()
        except requests.RequestException as e:
            raise DriverError(f"Emissor Tasmota não respondeu: {e}. {self.HINT}")


class IRBroadlink(_IR):
    HINT = ("Confira se o Broadlink (RM mini/pro) está no mesmo Wi-Fi, configurado no app Broadlink/IHC e "
            "com o bloqueio de dispositivo desativado.")

    def __init__(self, dev):
        super().__init__(dev)
        self.bl = None
        self.lock = threading.Lock()

    def close(self):
        self.bl = None

    def _dev(self):
        if self.bl is None:
            try:
                import broadlink
            except ImportError:
                raise DriverError("Falta instalar: pip install broadlink")
            d = broadlink.hello(self.host, timeout=4)
            d.auth()
            self.bl = d
        return self.bl

    def send(self, key):
        kind, val = self.lookup(key)
        data = val if kind == "learned" else broadlink_packet(*val)
        with self.lock:
            self._retry(lambda: self._dev().send_data(data), "Broadlink")

    def learn(self, key, timeout=25):
        """Aponte o controle original para o Broadlink e aperte a tecla."""
        with self.lock:
            def go():
                d = self._dev()
                d.enter_learning()
                end = time.time() + timeout
                while time.time() < end:
                    time.sleep(0.6)
                    try:
                        pkt = d.check_data()
                        if pkt:
                            return pkt
                    except Exception:  # noqa: BLE001 - sem dados ainda
                        continue
                raise DriverError("Não recebi nenhum sinal. Aponte o controle para o Broadlink, a ~5 cm, e aperte a tecla.")
            pkt = self._retry(go, "Broadlink")
        return "bl:" + base64.b64encode(pkt).decode()


DRIVERS = {
    "roku": Roku, "samsung": Samsung, "lg": LG, "sony": Sony,
    "androidtv": AndroidTV, "ir": IRTasmota, "broadlink": IRBroadlink,
}
TYPE_NAMES = {
    "roku": "Roku", "samsung": "Samsung", "lg": "LG webOS", "sony": "Sony Bravia",
    "androidtv": "Android TV / Google TV", "ir": "IR (Tasmota)", "broadlink": "IR (Broadlink)",
}


# --------------------------------------------------------------------------- rede
def wake_on_lan(mac):
    mac = mac.replace(":", "").replace("-", "").strip()
    if len(mac) != 12:
        raise DriverError("MAC inválido (use o formato AA:BB:CC:DD:EE:FF).")
    packet = bytes.fromhex("FF" * 6 + mac * 16)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for port in (9, 7):
            s.sendto(packet, ("255.255.255.255", port))


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _open(ip, port, timeout=0.45):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except OSError:
        return False
    finally:
        s.close()


def _ssdp(timeout=2.5):
    msg = ('M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\nMAN: "ssdp:discover"\r\n'
           "MX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    hosts = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.4)
    try:
        for _ in range(2):
            sock.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, (ip, _) = sock.recvfrom(4096)
                hosts[ip] = hosts.get(ip, "") + data.decode(errors="ignore").lower()
            except socket.timeout:
                continue
            except OSError:
                break
    except OSError as e:
        log(f"SSDP indisponível: {e}")
    finally:
        sock.close()
    return hosts


SCAN_PORTS = (8060, 8001, 8002, 3000, 3001, 6466, 6467, 55000, 80)


def _classify(ip, ports, ssdp_text):
    found = []
    if ports[8060]:
        try:
            x = ET.fromstring(requests.get(f"http://{ip}:8060/query/device-info", timeout=2).text)
            found.append({"type": "roku", "host": ip, "name": x.findtext("user-device-name") or "Roku"})
        except Exception:  # noqa: BLE001
            found.append({"type": "roku", "host": ip, "name": "Roku"})
    if ports[8001] or ports[8002]:
        name = "Samsung"
        try:
            d = requests.get(f"http://{ip}:8001/api/v2/", timeout=2).json().get("device", {})
            name = d.get("name", name)
        except Exception:  # noqa: BLE001
            pass
        found.append({"type": "samsung", "host": ip, "name": name})
    elif ports[55000]:
        found.append({"type": "samsung", "host": ip, "name": "Samsung antiga (porta 55000)",
                      "note": "Modelo anterior a 2016 pode não funcionar; prefira um emissor IR."})
    if ports[3000] or ports[3001]:
        found.append({"type": "lg", "host": ip, "name": "LG webOS" if ("lg" in ssdp_text or "webos" in ssdp_text) else "LG webOS (provável)"})
    if ports[6466] or ports[6467]:
        found.append({"type": "androidtv", "host": ip, "name": "Android TV / Google TV"})
    if ports[80]:
        try:
            j = requests.post(f"http://{ip}/sony/system", timeout=1.5,
                              json={"method": "getPowerStatus", "id": 1, "params": [], "version": "1.0"}).json()
            if "result" in j or "error" in j:
                found.append({"type": "sony", "host": ip, "name": "Sony Bravia"})
        except Exception:  # noqa: BLE001
            pass
        try:
            j = requests.get(f"http://{ip}/cm", params={"cmnd": "Status"}, timeout=1.5).json()
            if "Status" in j:
                found.append({"type": "ir", "host": ip, "name": "Emissor Tasmota: " + str(j["Status"].get("FriendlyName", ""))})
        except Exception:  # noqa: BLE001
            pass
    return found


def scan():
    """Procura TVs e emissores IR: SSDP + varredura da sub-rede /24 + Broadlink."""
    ssdp = _ssdp(2.0)
    ip = local_ip()
    base = ".".join(ip.split(".")[:3])
    hosts = [f"{base}.{i}" for i in range(1, 255)] if ip != "127.0.0.1" else []
    hosts = [h for h in dict.fromkeys(hosts + list(ssdp)) if h != ip]
    pairs = [(h, p) for h in hosts for p in SCAN_PORTS]
    open_ports = {}
    with ThreadPoolExecutor(max_workers=256) as ex:
        for (h, p), ok in zip(pairs, ex.map(lambda hp: _open(*hp, 0.4), pairs)):
            if ok:
                open_ports.setdefault(h, {})[p] = True
    results = []
    with ThreadPoolExecutor(max_workers=32) as ex:
        futs = [ex.submit(_classify, h, {p: open_ports[h].get(p, False) for p in SCAN_PORTS}, ssdp.get(h, ""))
                for h in open_ports]
        for f in futs:
            results += f.result()
    try:
        import broadlink
        for d in broadlink.discover(timeout=2):
            results.append({"type": "broadlink", "host": d.host[0], "name": f"Broadlink {getattr(d, 'type', '')}"})
    except Exception as e:  # noqa: BLE001
        log(f"Busca Broadlink: {e}")
    log(f"Busca terminou: {len(results)} dispositivo(s) em {base}.0/24")
    return results
