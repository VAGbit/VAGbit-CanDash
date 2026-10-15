"""VAGbit CAN Dashboard v0.4.3 - buffered CSV logging and fast live telemetry."""
import argparse
import queue
import csv
from datetime import datetime
import json
import re
import threading
import time
import webbrowser
from collections import Counter, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
BAUD = 921600
SERIAL = None
STOP = threading.Event()
LOCK = threading.Lock()
STATE = {
    'mode': 'idle', 'connection': 'Not connected', 'error': '',
    'frames': 0, 'invalid': 0, 'unique': 0, 'last_frame': 0,
    'signals': {}, 'ids': {}, 'recent': [], 'stats': {}, 'source': ''
}
SEEN = Counter()
LATEST = {}
RECENT = deque(maxlen=30)

# Write CSV on a separate thread; the CAN receive path never waits for disk I/O.
LOG_DIR = ROOT / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_LOCK = threading.Lock()
LOG_QUEUE = queue.Queue(maxsize=20000)
LOG_STOP = threading.Event()
LOG_THREAD = None
LOG_INFO = {'active': False, 'filename': '', 'frames': 0, 'dropped': 0,
            'error': '', 'started': '', 'source': ''}

def log_snapshot():
    with LOG_LOCK:
        result = dict(LOG_INFO)
    result['pending'] = LOG_QUEUE.qsize()
    return result

def log_worker(path):
    """Write bounded batches and update shared counters once per batch."""
    try:
        with path.open('w', newline='', encoding='ascii', buffering=512 * 1024) as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(['timestamp_ms', 'can_id', 'extended', 'dlc', 'data'])
            last_flush = time.monotonic()
            while not LOG_STOP.is_set() or not LOG_QUEUE.empty():
                try:
                    first = LOG_QUEUE.get(timeout=0.15)
                except queue.Empty:
                    first = None
                if first is not None:
                    batch = [first]
                    for _ in range(999):
                        try:
                            batch.append(LOG_QUEUE.get_nowait())
                        except queue.Empty:
                            break
                    writer.writerows(batch)
                    with LOG_LOCK:
                        LOG_INFO['frames'] += len(batch)
                    for _ in batch:
                        LOG_QUEUE.task_done()
                if time.monotonic() - last_flush >= 1:
                    handle.flush()
                    last_flush = time.monotonic()
            handle.flush()
    except Exception as exc:
        with LOG_LOCK:
            LOG_INFO['error'] = str(exc)
    finally:
        with LOG_LOCK:
            LOG_INFO['active'] = False

def stop_logging():
    global LOG_THREAD
    with LOG_LOCK:
        active_thread = LOG_THREAD
        LOG_INFO['active'] = False
        LOG_STOP.set()
    if active_thread is not None and active_thread is not threading.current_thread():
        active_thread.join(timeout=12)
    with LOG_LOCK:
        if active_thread is not None and active_thread.is_alive():
            return {'error': 'Logger is still flushing data. Try again shortly.'}
        LOG_THREAD = None
        return dict(LOG_INFO)

def start_logging():
    global LOG_THREAD, LOG_QUEUE
    with LOG_LOCK:
        if LOG_THREAD is not None and LOG_THREAD.is_alive():
            return {'error': 'A logging session is already active or finishing.'}
        LOG_QUEUE = queue.Queue(maxsize=20000)
        LOG_STOP.clear()
        filename = 'VAGbit_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.csv'
        LOG_INFO.update(active=True, filename=filename, frames=0, dropped=0,
                        error='', started=datetime.now().astimezone().isoformat(timespec='seconds'),
                        source=STATE.get('mode', 'idle'))
        LOG_THREAD = threading.Thread(target=log_worker, args=(LOG_DIR / filename,), daemon=True)
        LOG_THREAD.start()
        return dict(LOG_INFO)

def enqueue_frame(timestamp, ident, extended, data):
    with LOG_LOCK:
        if not LOG_INFO['active'] or LOG_STOP.is_set():
            return
        try:
            LOG_QUEUE.put_nowait((timestamp, f'{ident:03X}', int(extended), len(data),
                                  ' '.join(f'{byte:02X}' for byte in data)))
        except queue.Full:
            LOG_INFO['dropped'] += 1

# Source profiles: PQ46 VW CAN summary and historical VAG/T4 CAN documentation.
# Nothing below is a proven PQ25 vehicle-specific signal without validation.
SIGNALS = [
    ('rpm', 'Engine RPM', 'rpm', '280', 'b2+b3*256 / 4', 'candidate', 0, 7500),
    ('torque', 'Engine torque estimate', '%', '280', 'b1 * 0.39', 'experimental', 0, 100),
    ('driver_torque', 'Driver request estimate', '%', '280', 'b7 * 0.4', 'experimental', 0, 100),
    ('pedal_ecu', 'Accelerator ECU', '%', '280', 'b5 * 0.4', 'experimental', 0, 100),
    ('coolant', 'Coolant temperature', '°C', '288', 'b1 * 0.75 - 48', 'candidate', -20, 140),
    ('idle_setpoint', 'Idle RPM request', 'rpm', '288', 'b5 * 10', 'experimental', 0, 2000),
    ('torque_limit', 'Torque limit estimate', '%', '288', 'b6 * 0.39', 'experimental', 0, 100),
    ('intake', 'Intake air temperature', '°C', '380', 'b1 * 0.75 - 48', 'candidate', -20, 140),
    ('pedal', 'Accelerator position', '%', '380', 'b2 * 0.4', 'candidate', 0, 100),
    ('oil', 'Oil temperature', '°C', '588', 'b7 - 60', 'candidate', 0, 160),
    ('boost_raw', 'Boost raw byte', 'raw', '588', 'b4', 'candidate', 0, 255),
    ('boost_abs', 'Boost absolute estimate', 'bar', '588', 'b4 * 0.02', 'experimental', 0, 3.5),
    ('speed', 'Vehicle speed estimate', 'km/h', '320', 'LE16(b3,b4) * 0.01', 'experimental', 0, 300),
    ('fuel_raw', 'Fuel level raw', 'raw', '320', 'b2 & 0x7F', 'candidate', 0, 127),
    ('odometer', 'Odometer raw', 'raw', '520', 'LE24(b5,b6,b7)', 'experimental', 0, 1000000),
    ('consumption_raw', 'Fuel consumption raw', 'raw', '480', 'b2 + ((b3 & 0x7F)<<8)', 'experimental', 0, 32767),
    ('backlight', 'Backlight raw', 'raw', '470', 'b4', 'candidate', 0, 255),
    ('gear_raw', 'Gear recommendation raw', 'raw', '540', 'b7', 'experimental', 0, 255),
    ('brake_raw', 'Brake pressure raw', 'raw', '4A8', 'LE16(b2,b3)', 'experimental', 0, 65535),
    ('steering_raw', 'Steering wheel raw', 'raw', '0C2', 'LE16(b0,b1)', 'experimental', 0, 65535),
    ('wheel_fl_raw', 'Wheel FL raw', 'raw', '4A0', 'LE16(b0,b1)', 'experimental', 0, 65535),
    ('wheel_fr_raw', 'Wheel FR raw', 'raw', '4A0', 'LE16(b2,b3)', 'experimental', 0, 65535),
    ('wheel_rl_raw', 'Wheel RL raw', 'raw', '4A0', 'LE16(b4,b5)', 'experimental', 0, 65535),
    ('wheel_rr_raw', 'Wheel RR raw', 'raw', '4A0', 'LE16(b6,b7)', 'experimental', 0, 65535),
]

BIT_SIGNALS = [
    ('clutch', 'Clutch switch', '280', 0, 3),
    ('brake', 'Brake pedal', '1A0', 1, 3),
    ('cruise', 'Cruise status', '288', 2, 6),
    ('driver_door', 'Driver door', '390', 2, 0),
    ('rear_left_door', 'Rear left door', '390', 3, 2),
    ('rear_right_door', 'Rear right door', '390', 3, 3),
    ('reverse', 'Reverse light', '390', 3, 4),
    ('indicator_left', 'Left indicator', '390', 4, 2),
    ('indicator_right', 'Right indicator', '390', 4, 3),
    ('front_right_door', 'Front right door', '390', 5, 1),
    ('trunk', 'Trunk', '390', 5, 3),
    ('lowbeam', 'Low beam', '390', 6, 0),
    ('highbeam', 'High beam', '390', 6, 1),
    ('hazards', 'Hazard lights', '390', 6, 7),
    ('wipers', 'Wipers', '390', 7, 2),
    ('brake_light', 'Brake lights', '390', 7, 3),
    ('auto_lights', 'Auto lights', '392', 4, 5),
    ('rear_fog', 'Rear fog light', '392', 4, 7),
]

CONFIG = {'mode': None, 'port': '', 'log': '', 'baud': BAUD, 'replay_speed': 1.0}

# The logger emits either "timestamp,id,extended,dlc,data" or "CAN,timestamp,id,dlc,data".
HEX_RE = re.compile(r'^[0-9A-Fa-f]{1,8}$')

def parse_frame(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    if line.startswith('CAN,'):
        fields = line.split(',', 4)
        if len(fields) != 5:
            return False
        _, ts, can_id, dlc, payload = fields
        extended = '0'
    else:
        fields = line.split(',', 4)
        if len(fields) != 5:
            return False
        ts, can_id, extended, dlc, payload = fields
    try:
        timestamp = int(ts)
        count = int(dlc)
        if not HEX_RE.fullmatch(can_id) or count < 0 or count > 8:
            return False
        if extended not in ('0', '1'):
            return False
        ident = int(can_id, 16)
        if ident > (0x1FFFFFFF if extended == '1' else 0x7FF):
            return False
        parts = payload.strip().split() if payload.strip() else []
        if len(parts) != count or any(len(b) != 2 or not HEX_RE.fullmatch(b) for b in parts):
            return False
        return timestamp, ident, bool(int(extended)), bytes.fromhex(''.join(parts))
    except (ValueError, OverflowError):
        return False

def decode(ident, data, now):
    sig = {}
    def put(k, value):
        sig[k] = {'value': round(value, 3), 'at': now}
    def le(a, b):
        return data[a] + (data[b] << 8)
    if ident == 0x280 and len(data) == 8:
        put('rpm', le(2, 3) / 4)
        put('torque', data[1] * .39)
        put('driver_torque', data[7] * .4)
        put('pedal_ecu', data[5] * .4)
    if ident == 0x288 and len(data) == 8:
        if data[1] != 0xFF:
            put('coolant', data[1] * .75 - 48)
        put('idle_setpoint', data[5] * 10)
        put('torque_limit', data[6] * .39)
    if ident == 0x380 and len(data) == 8:
        if data[1] != 0xFF:
            put('intake', data[1] * .75 - 48)
        put('pedal', data[2] * .4)
    if ident == 0x588 and len(data) == 8:
        put('oil', data[7] - 60)
        put('boost_raw', data[4])
        put('boost_abs', data[4] * .02)
    if ident == 0x320 and len(data) == 8:
        put('speed', le(3, 4) * .01)
        put('fuel_raw', data[2] & 0x7F)
    if ident == 0x520 and len(data) == 8:
        put('odometer', data[5] + (data[6] << 8) + (data[7] << 16))
    if ident == 0x480 and len(data) == 8:
        put('consumption_raw', data[2] + ((data[3] & 0x7F) << 8))
    if ident == 0x470 and len(data) == 8:
        put('backlight', data[4])
    if ident == 0x540 and len(data) == 8:
        put('gear_raw', data[7])
    if ident == 0x4A8 and len(data) == 8:
        put('brake_raw', le(2, 3))
    if ident == 0x0C2 and len(data) == 8:
        put('steering_raw', le(0, 1))
    if ident == 0x4A0 and len(data) == 8:
        for k, (a,b) in zip(('wheel_fl_raw','wheel_fr_raw','wheel_rl_raw','wheel_rr_raw'), ((0,1),(2,3),(4,5),(6,7))):
            put(k, le(a,b))
    for name, label, key, byte, bit in BIT_SIGNALS:
        if ident == int(key, 16) and len(data) > byte:
            put(name, (data[byte] >> bit) & 1)
    return sig

def ingest(line):
    if line.startswith('# STATS'):
        vals = dict(re.findall(r'(\w+)=(\d+)', line))
        with LOCK:
            STATE['stats'] = vals
        return
    parsed = parse_frame(line)
    if parsed is None:
        return
    now = time.monotonic()
    if parsed is False:
        with LOCK:
            STATE['invalid'] += 1
        return
    timestamp, ident, extended, data = parsed
    enqueue_frame(timestamp, ident, extended, data)
    frame = {'timestamp': timestamp, 'id': f'{ident:03X}', 'extended': extended,
             'dlc': len(data), 'data': ' '.join(f'{b:02X}' for b in data)}
    with LOCK:
        STATE['frames'] += 1
        STATE['last_frame'] = now
        key = frame['id']
        SEEN[key] += 1
        LATEST[key] = frame
        RECENT.appendleft(frame)
        STATE['signals'].update(decode(ident, data, now))

def serial_worker(port, baud):
    global SERIAL
    try:
        import serial
        conn = serial.Serial()
        conn.port = port
        conn.baudrate = baud
        conn.timeout = 0.25
        conn.write_timeout = 0.5
        conn.dtr = False
        conn.rts = False
        conn.open()
        with conn:
            SERIAL = conn
            with LOCK:
                STATE['mode'] = 'serial'
                STATE['connection'] = f'Connected: {port} @ {baud}'
            while not STOP.is_set():
                raw = conn.readline()
                if raw:
                    ingest(raw.decode('ascii', errors='replace').strip())
    except Exception as e:
        with LOCK:
            STATE['error'] = str(e)
            STATE['connection'] = 'Disconnected'
    finally:
        SERIAL = None

REPLAY_MAX_FPS = 350  # Prevent CPU saturation on huge CAN captures.

def replay_worker(filename, speed):
    path = Path(filename)
    if not path.is_file():
        with LOCK:
            STATE['error'] = f'File not found: {filename}'
        return
    with LOCK:
        STATE['mode'] = 'replay'
        STATE['connection'] = f'Replaying: {path.name}'
    try:
        prev_ts = None
        next_frame_due = time.monotonic()
        with path.open('r', errors='replace', buffering=256 * 1024) as file:
            for line in file:
                if STOP.is_set():
                    break
                frame = parse_frame(line)
                if frame:
                    # Bound processing and UI update pressure even if input timestamps
                    # are identical, malformed, or much faster than real time.
                    delay = 1.0 / REPLAY_MAX_FPS
                    if prev_ts is not None:
                        delta_ms = frame[0] - prev_ts
                        if 0 < delta_ms < 2000:
                            delay = max(delay, min(delta_ms / 1000.0 / speed, 0.05))
                    next_frame_due = max(next_frame_due + delay, time.monotonic())
                    wait = next_frame_due - time.monotonic()
                    if wait > 0 and STOP.wait(wait):
                        break
                    prev_ts = frame[0]
                ingest(line.strip())
        with LOCK:
            STATE['connection'] = 'Replay completed'
    except Exception as e:
        with LOCK:
            STATE['error'] = str(e)
            STATE['connection'] = 'Replay failed'

class Handler(BaseHTTPRequestHandler):
    def handle(self):
        # Browsers can cancel outstanding polling requests on refresh or navigation.
        # This is expected and must not produce a traceback in the server console.
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
    def log_message(self, fmt, *args):
        pass
    def respond(self, payload, code=200):
        blob = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(blob)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(blob)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/live':
            # Small response for responsive gauges. Never serialize the full ID table here.
            with LOCK:
                result = {
                    'signals': dict(STATE['signals']),
                    'frames': STATE['frames'],
                    'last_frame': STATE['last_frame'],
                    'mode': STATE['mode'],
                    'connection': STATE['connection'],
                    'error': STATE['error'],
                }
            result['age'] = round(time.monotonic() - result['last_frame'], 2) if result['last_frame'] else None
            self.respond(result)
        elif path == '/api/state':
            with LOCK:
                state = dict(STATE)
                state['signals'] = dict(STATE['signals'])
                state['ids'] = dict(SEEN)
                state['recent'] = list(RECENT)
                state['latest'] = dict(LATEST)  # Bounded by number of CAN IDs (max 2048 standard IDs)
                state['unique'] = len(SEEN)
            state['age'] = round(time.monotonic() - state['last_frame'], 2) if state['last_frame'] else None
            state['logging'] = log_snapshot()
            self.respond(state)
        elif path == '/api/ports':
            try:
                from serial.tools import list_ports
                ports = [{'device': p.device, 'description': p.description} for p in list_ports.comports()]
            except ImportError:
                ports = []
            self.respond({'ports': ports})
        elif path == '/api/logging':
            self.respond(log_snapshot())
        elif path == '/api/logs':
            self.respond({'files': [dict(name=f.name, bytes=f.stat().st_size)
                                    for f in sorted(LOG_DIR.glob('VAGbit_*.csv'), reverse=True)[:100]]})
        elif path.startswith('/api/logs/download/'):
            from urllib.parse import unquote
            filename = unquote(path.rsplit('/', 1)[-1])
            if not re.fullmatch(r'VAGbit_\d{8}_\d{6}_\d{6}\.csv', filename):
                self.send_error(400, 'Invalid log filename')
                return
            file = LOG_DIR / filename
            if not file.is_file():
                self.send_error(404, 'Log not found')
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/csv; charset=utf-8')
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Content-Length', str(file.stat().st_size))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            with file.open('rb') as handle:
                while chunk := handle.read(65536):
                    self.wfile.write(chunk)
        elif path == '/api/schema':
            self.respond({'signals': [dict(zip(('key','label','unit','id','formula','confidence','min','max'), s)) for s in SIGNALS],
                          'bits': [dict(zip(('key','label','id','byte','bit'), s)) for s in BIT_SIGNALS]})
        elif path == '/':
            content = (ROOT / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)
    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/logging/start':
            return self.respond(start_logging())
        if path == '/api/logging/stop':
            result = stop_logging()
            return self.respond(result, 409 if result.get('error') else 200)
        if path != '/api/connect':
            self.send_error(404)
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            args = json.loads(self.rfile.read(min(length, 4096)))
            kind = args.get('mode')
            if kind not in ('serial', 'replay'):
                return self.respond({'error': 'Invalid connection mode'}, 400)
            STOP.set()
            if SERIAL:
                try:
                    SERIAL.cancel_read()
                except Exception:
                    pass
            time.sleep(.1)
            STOP.clear()
            with LOCK:
                SEEN.clear()
                LATEST.clear()
                RECENT.clear()
                STATE['signals'] = {}
                STATE['frames'] = 0
                STATE['invalid'] = 0
                STATE['stats'] = {}
                STATE['error'] = ''
                STATE['last_frame'] = 0
            if kind == 'serial':
                port = str(args.get('port', '')).strip()
                baud = int(args.get('baud', BAUD))
                if not port or not 300 <= baud <= 3000000:
                    return self.respond({'error': 'Invalid serial configuration'}, 400)
                args_thread = (port, baud)
                target = serial_worker
            else:
                filename = str(args.get('file', ROOT / 'sample_log.txt'))
                speed = float(args.get('speed', 1))
                if not .1 <= speed <= 20:
                    return self.respond({'error': 'Invalid replay speed'}, 400)
                if not Path(filename).is_file():
                    return self.respond({'error': 'Replay file not found'}, 400)
                args_thread = (filename, speed)
                target = replay_worker
            threading.Thread(target=target, args=args_thread, daemon=True).start()
            self.respond({'ok': True})
        except Exception as e:
            self.respond({'error': str(e)}, 400)

def main():
    parser = argparse.ArgumentParser(description='VAGbit CAN Dashboard')
    parser.add_argument('--port', default='', help='COM port, such as COM16')
    parser.add_argument('--baud', type=int, default=BAUD)
    parser.add_argument('--replay', default='', help='CAN logger text file')
    parser.add_argument('--http-port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.http_port), Handler)
    url = f'http://127.0.0.1:{args.http_port}/'
    print('VAGbit CAN Dashboard v0.4.1')
    print('Dashboard:', url)
    print('Local only. No CAN frames are transmitted.')
    if args.replay:
        threading.Thread(target=replay_worker, args=(args.replay, 1), daemon=True).start()
    elif args.port:
        threading.Thread(target=serial_worker, args=(args.port, args.baud), daemon=True).start()
    if not args.no_browser:
        threading.Timer(.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP.set()
        stop_logging()
        server.server_close()

if __name__ == '__main__':
    main()
