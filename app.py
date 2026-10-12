"""Controle remoto universal: servidor web. Rode `python app.py` e abra o endereço no celular."""
import json
import threading
import uuid

from flask import Flask, jsonify, request, send_from_directory

import drivers

BASE = drivers.Path(__file__).parent
DEVICES_FILE = drivers.DATA / "devices.json"
app = Flask(__name__, static_folder=str(BASE / "static"))
lock = threading.Lock()
cache = {}

EDITABLE = ("name", "host", "mac", "psk", "ir_preset", "ir_codes", "type")


def load():
    if DEVICES_FILE.exists():
        try:
            return json.loads(DEVICES_FILE.read_text())
        except ValueError:
            return []
    return []


def save(devs):
    drivers.DATA.mkdir(parents=True, exist_ok=True)
    DEVICES_FILE.write_text(json.dumps(devs, indent=2, ensure_ascii=False))


def find(dev_id):
    return next((d for d in load() if d["id"] == dev_id), None)


def driver_for(dev):
    cached = cache.get(dev["id"])
    if cached is None or cached.dev != dev:
        if cached:
            cached.close()
        cls = drivers.DRIVERS.get(dev["type"])
        if cls is None:
            raise drivers.DriverError(f"Tipo desconhecido: {dev['type']}")
        cached = cache[dev["id"]] = cls(dev)
    return cached


def fail(msg, code=400):
    drivers.log(f"ERRO: {msg}")
    return jsonify(ok=False, error=msg), code


def with_driver(dev_id, fn):
    dev = find(dev_id)
    if not dev:
        return fail("TV não encontrada. Atualize a página.", 404)
    try:
        out = fn(driver_for(dev), dev)
    except drivers.DriverError as e:
        return fail(str(e), 502)
    except Exception as e:  # noqa: BLE001
        return fail(f"{type(e).__name__}: {e}", 500)
    return jsonify(ok=True, **(out or {}))


@app.errorhandler(Exception)
def on_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify(ok=False, error=e.description), e.code
    import traceback
    traceback.print_exc()
    return fail(f"Erro interno: {type(e).__name__}: {e}", 500)


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/types")
def types():
    return jsonify({"types": drivers.TYPE_NAMES, "presets": drivers.PRESET_NAMES,
                    "needs_pairing": [k for k, c in drivers.DRIVERS.items() if c.needs_pairing],
                    "keys": drivers.KEYS})


@app.get("/api/devices")
def list_devices():
    return jsonify(load())


@app.post("/api/devices")
def add_device():
    body = request.get_json(force=True)
    if body.get("type") not in drivers.DRIVERS:
        return fail("Escolha o tipo da TV.")
    if not (body.get("host") or "").strip():
        return fail("Informe o IP da TV (ou do emissor IR).")
    dev = {"id": uuid.uuid4().hex[:8], "type": body["type"], "host": body["host"].strip(),
           "name": (body.get("name") or "").strip() or drivers.TYPE_NAMES[body["type"]]}
    for extra in ("mac", "psk", "ir_preset", "ir_codes"):
        if body.get(extra):
            dev[extra] = body[extra]
    with lock:
        devs = load()
        devs.append(dev)
        save(devs)
    drivers.log(f"TV adicionada: {dev['name']} ({dev['type']} @ {dev['host']})")
    return jsonify(dev)


@app.patch("/api/devices/<dev_id>")
def edit_device(dev_id):
    body = request.get_json(force=True)
    with lock:
        devs = load()
        dev = next((d for d in devs if d["id"] == dev_id), None)
        if not dev:
            return fail("TV não encontrada.", 404)
        for k in EDITABLE:
            if k in body:
                if body[k] in ("", None):
                    dev.pop(k, None) if k not in ("name", "host", "type") else None
                else:
                    dev[k] = body[k].strip() if isinstance(body[k], str) else body[k]
        save(devs)
    c = cache.pop(dev_id, None)
    if c:
        c.close()
    return jsonify(dev)


@app.post("/api/devices/<dev_id>/reset")
def reset_device(dev_id):
    """Esquece pareamento/tokens desta TV (mantém o cadastro)."""
    dev = find(dev_id)
    if not dev:
        return fail("TV não encontrada.", 404)
    c = cache.pop(dev_id, None) or drivers.DRIVERS[dev["type"]](dev)
    c.reset()
    drivers.log(f"Conexão resetada: {dev['name']}")
    return jsonify(ok=True)


@app.delete("/api/devices/<dev_id>")
def del_device(dev_id):
    dev = find(dev_id)
    if dev:
        c = cache.pop(dev_id, None) or drivers.DRIVERS[dev["type"]](dev)
        c.reset()
    with lock:
        save([d for d in load() if d["id"] != dev_id])
    return jsonify(ok=True)


@app.post("/api/reset_all")
def reset_all():
    """Apaga todas as TVs, tokens e certificados. Recomeço do zero."""
    for c in list(cache.values()):
        c.close()
    cache.clear()
    with lock:
        for f in drivers.DATA.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass
    drivers.log("Tudo apagado; recomeçando do zero.")
    return jsonify(ok=True)


@app.get("/api/scan")
def scan():
    try:
        return jsonify(drivers.scan())
    except Exception as e:  # noqa: BLE001
        return fail(f"Falha na busca: {e}", 500)


@app.get("/api/log")
def get_log():
    return jsonify(list(drivers.LOG))


@app.post("/api/devices/<dev_id>/key/<key>")
def press(dev_id, key):
    if key not in drivers.KEYS:
        return fail("Tecla inválida.")

    def go(drv, dev):
        try:
            drv.send(key)
        except drivers.DriverError:
            # TV desligada não responde: tenta ligar por Wake-on-LAN se houver MAC
            if key == "power" and dev.get("mac"):
                drivers.wake_on_lan(dev["mac"])
                drivers.log(f"Wake-on-LAN enviado para {dev['name']}")
            else:
                raise
    return with_driver(dev_id, go)


@app.get("/api/devices/<dev_id>/apps")
def apps(dev_id):
    return with_driver(dev_id, lambda drv, dev: {"apps": drv.apps()})


@app.post("/api/devices/<dev_id>/launch")
def launch(dev_id):
    app_id = (request.get_json(force=True) or {}).get("id")
    if not app_id:
        return fail("Informe o app.")
    return with_driver(dev_id, lambda drv, dev: drv.launch(app_id))


@app.post("/api/devices/<dev_id>/text")
def text(dev_id):
    b = request.get_json(force=True) or {}
    action, delta, full = b.get("action", "type"), b.get("delta", ""), b.get("full", "")

    def go(drv, dev):
        if action == "type":
            drv.type(delta, full)
        elif action == "erase":
            drv.erase(int(b.get("n", 1)), full)
        elif action == "enter":
            drv.enter()
        else:
            raise drivers.DriverError("Ação inválida.")
    return with_driver(dev_id, go)


@app.post("/api/devices/<dev_id>/test")
def test(dev_id):
    return with_driver(dev_id, lambda drv, dev: {"result": drv.test()})


@app.post("/api/devices/<dev_id>/learn/<key>")
def learn(dev_id, key):
    if key not in drivers.KEYS:
        return fail("Tecla inválida.")

    def go(drv, dev):
        if not hasattr(drv, "learn"):
            raise drivers.DriverError("Só emissores Broadlink aprendem teclas.")
        code = drv.learn(key)
        with lock:
            devs = load()
            for d in devs:
                if d["id"] == dev_id:
                    d.setdefault("ir_codes", {})[key] = code
            save(devs)
        cache.pop(dev_id, None)
        drivers.log(f"Tecla '{key}' aprendida em {dev['name']}")
    return with_driver(dev_id, go)


@app.post("/api/devices/<dev_id>/pair/start")
def pair_start(dev_id):
    return with_driver(dev_id, lambda drv, dev: drv.pair_start())


@app.post("/api/devices/<dev_id>/pair/finish")
def pair_finish(dev_id):
    code = (request.get_json(force=True) or {}).get("code", "")
    return with_driver(dev_id, lambda drv, dev: drv.pair_finish(code))


if __name__ == "__main__":
    port = 5000
    print(f"\n  Abra no celular (mesmo Wi-Fi da TV):  http://{drivers.local_ip()}:{port}\n")
    app.run(host="0.0.0.0", port=port, threaded=True)
