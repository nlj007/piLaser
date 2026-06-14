from flask import Flask, render_template_string, jsonify, request, send_from_directory
import os
import json
import time
import subprocess
import urllib.request
import urllib.error
import urllib.parse
import psutil  # sudo apt install python3-psutil

# GPIO optional so server can start without Pi hardware (e.g. remote dev)
# Use lgpio for Pi 5 compatibility (same as working fade script)
lgpio_handle = None
GPIO_AVAILABLE = False
try:
    import lgpio
    lgpio_handle = lgpio.gpiochip_open(0)
    GPIO_AVAILABLE = True
except Exception as e:
    lgpio_handle = None
    GPIO_AVAILABLE = False

# Load .env from same directory as this script (optional local overrides)
_base_dir = os.path.abspath(os.path.dirname(__file__))
_env_path = os.path.join(_base_dir, ".env")
if os.path.isfile(_env_path):
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v

EQ_SERVICE_URL = "http://127.0.0.1:6970"
NOW_PLAYING_API = "http://127.0.0.1:8777"

app = Flask(__name__)

# ===== GPIO + PWM SETUP (optional on non-Pi) =====
# Confirmed working pins (replacement board, verified 2026-06-15):
# Avoid GPIO 20 — Pi 5 power button.
RED_PIN   = 12  # Pin 32
GREEN_PIN = 16  # Pin 36
BLUE_PIN  = 21  # Pin 40
# GND rail on Pin 39.
FREQ = 10000  # 10 kHz PWM

if GPIO_AVAILABLE and lgpio_handle is not None:
    try:
        for _pin in (RED_PIN, GREEN_PIN, BLUE_PIN):
            try:
                lgpio.gpio_free(lgpio_handle, _pin)
            except Exception:
                pass
            lgpio.gpio_claim_output(lgpio_handle, _pin, 1)  # level=1: GPIO HIGH → transistor ON → laser OFF (active-low)
    except Exception as e:
        print(f"GPIO setup error: {e}", flush=True)
        GPIO_AVAILABLE = False

# base_color: internal 0–100 scale where 0 = full intensity, 100 = off
# (inverted from RGB: r=255 → base_color['r']=0, r=0 → base_color['r']=100)
base_color = {'r': 100.0, 'g': 100.0, 'b': 100.0}
laser_on = True   # toggle state; True + all base_color=100 → laser fires nothing (safe default)
brightness = 100.0  # 0–100 %

def _pin_write(pin, level):
    """Set pin to a clean DC level, stopping any active PWM first."""
    try:
        lgpio.gpio_free(lgpio_handle, pin)
    except Exception:
        pass
    lgpio.gpio_claim_output(lgpio_handle, pin, level)

def apply_color():
    if not GPIO_AVAILABLE or lgpio_handle is None:
        return
    try:
        if not laser_on:
            for _p in (RED_PIN, GREEN_PIN, BLUE_PIN):
                _pin_write(_p, 1)  # GPIO HIGH → transistor ON → laser OFF (active-low)
            return
        scale = brightness / 100.0
        for _pin, _base in ((RED_PIN, base_color['r']), (GREEN_PIN, base_color['g']), (BLUE_PIN, base_color['b'])):
            bval = (100.0 - _base) * scale  # 0=off, 100=full brightness
            if bval <= 0.0:
                _pin_write(_pin, 1)   # fully off: clean digital HIGH → transistor ON → laser OFF
            elif bval >= 100.0:
                _pin_write(_pin, 0)   # fully on: clean digital LOW → transistor OFF → laser ON
            else:
                # ACTIVE-LOW: invert duty — 0.0 = transistor OFF = laser ON, 1.0 = transistor ON = laser OFF
                lgpio.tx_pwm(lgpio_handle, _pin, FREQ, 1.0 - bval / 100.0)
    except Exception as e:
        print(f"apply_color error: {e}", flush=True)

def set_base_color_from_rgb8(r8, g8, b8):
    r = max(0, min(255, int(r8)))
    g = max(0, min(255, int(g8)))
    b = max(0, min(255, int(b8)))
    base_color['r'] = 100.0 - (r / 255.0) * 100.0
    base_color['g'] = 100.0 - (g / 255.0) * 100.0
    base_color['b'] = 100.0 - (b / 255.0) * 100.0
    apply_color()

# ===== PI STATUS =====
start_time = time.time()

def get_cpu_temp_c():
    try:
        out = subprocess.check_output(["vcgencmd", "measure_temp"], text=True)
        return float(out.replace("temp=", "").replace("'C", "").strip())
    except Exception:
        return 0.0

def get_cpu_load_pct():
    return psutil.cpu_percent(interval=0.1)

def get_uptime_sec():
    return int(time.time() - start_time)

# ===== PWA STATIC =====
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

manifest_path = os.path.join(STATIC_DIR, "manifest.json")
if not os.path.exists(manifest_path):
    manifest = {
        "name": "Laser Control",
        "short_name": "Laser",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#6CA6CD",
        "theme_color": "#245EDC",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }
    with open(manifest_path, "w") as f:
        json.dump(manifest, f)

sw_path = os.path.join(STATIC_DIR, "sw.js")
if not os.path.exists(sw_path):
    sw_code = """
self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => clients.claim());
self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));
"""
    with open(sw_path, "w") as f:
        f.write(sw_code)

HTML = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <title>Laser Control</title>

    <link rel="manifest" href="/manifest.json">
    <meta name="theme-color" content="#245EDC">
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="default">
    <meta name="apple-mobile-web-app-title" content="Laser Control">
    <link rel="apple-touch-icon" href="/static/icon-192.png">

    <style>
        /* Windows XP Luna–inspired shell (visual reference: classic XP GUI; PDF not on device) */
        :root {
            --xp-face: #ece9d8;
            --xp-face-mid: #d4d0c8;
            --xp-shadow: #808080;
            --xp-deep: #404040;
            --xp-hilite: #ffffff;
            --xp-title-1: #0a246a;
            --xp-title-2: #a6caf0;
            --xp-link: #000080;
            --xp-green: #008000;
            --xp-red: #800000;
            /* CRT overscan: keep UI inside ~93% of the tube */
            --crt-safe: clamp(10px, 3.2vmin, 36px);
        }
        html, body {
            margin: 0;
            padding: 0;
            height: 100%;
            min-height: 100vh;
            min-height: 100dvh;
            overflow: hidden;
            touch-action: manipulation;
            font-family: "Tahoma", "MS Sans Serif", "Segoe UI", sans-serif;
            font-size: 11px;
            color: #000000;
        }
        .xp-desktop {
            min-height: 100vh;
            min-height: 100dvh;
            box-sizing: border-box;
            padding: calc(var(--crt-safe) + env(safe-area-inset-top, 0px))
                     calc(var(--crt-safe) + env(safe-area-inset-right, 0px))
                     calc(var(--crt-safe) + env(safe-area-inset-bottom, 0px))
                     calc(var(--crt-safe) + env(safe-area-inset-left, 0px));
            display: flex;
            align-items: center;
            justify-content: center;
            background:
                radial-gradient(ellipse 120% 80% at 20% 70%, rgba(255,255,255,0.35) 0%, transparent 50%),
                radial-gradient(ellipse 100% 60% at 85% 30%, rgba(144, 200, 255, 0.45) 0%, transparent 45%),
                linear-gradient(165deg, #5a9fd4 0%, #7ec0ee 35%, #4a8eb8 70%, #3d7aaa 100%);
        }
        /* Letterbox / pillarbox so the “monitor” is always 4:3 */
        .xp-crt-43 {
            width: min(
                calc(100vw - 2 * var(--crt-safe) - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px)),
                calc((100vh - 2 * var(--crt-safe) - env(safe-area-inset-top, 0px) - env(safe-area-inset-bottom, 0px)) * 4 / 3)
            );
            height: min(
                calc(100vh - 2 * var(--crt-safe) - env(safe-area-inset-top, 0px) - env(safe-area-inset-bottom, 0px)),
                calc((100vw - 2 * var(--crt-safe) - env(safe-area-inset-left, 0px) - env(safe-area-inset-right, 0px)) * 3 / 4)
            );
            max-width: 100%;
            max-height: 100%;
            display: flex;
            flex-direction: column;
            min-height: 0;
        }
        .xp-window {
            flex: 1;
            min-height: 0;
            width: 100%;
            margin: 0;
            display: flex;
            flex-direction: column;
            background: var(--xp-face);
            border: 1px solid var(--xp-deep);
            box-shadow:
                inset 1px 1px 0 var(--xp-hilite),
                inset -1px -1px 0 var(--xp-shadow),
                4px 4px 12px rgba(0,0,0,0.25);
        }
        .xp-titlebar {
            display: flex;
            align-items: center;
            gap: 6px;
            height: 29px;
            padding: 0 4px 0 6px;
            background: linear-gradient(180deg, #1c5fa8 0%, #2980d8 12%, #3c9ce8 50%, #2568c4 92%, #1a4e90 100%);
            border-bottom: 1px solid #003878;
            user-select: none;
        }
        .xp-titlebar-icon {
            width: 16px;
            height: 16px;
            background: linear-gradient(135deg, #ff6 0%, #c90 40%, #960 100%);
            border: 1px solid #630;
            border-radius: 2px;
            flex-shrink: 0;
        }
        .xp-titlebar-text {
            flex: 1;
            font-weight: bold;
            font-size: 11px;
            color: #fff;
            text-shadow: 1px 1px 0 rgba(0,0,0,0.45);
            letter-spacing: 0.02em;
        }
        .xp-titlebar-ctrl {
            display: flex;
            gap: 2px;
        }
        .xp-sysbtn {
            width: 21px;
            height: 21px;
            padding: 0;
            border: 1px solid #000;
            background: linear-gradient(180deg, #5c9fd9 0%, #3a7ec4 45%, #2a6cb0 100%);
            color: #fff;
            font-size: 10px;
            line-height: 1;
            cursor: default;
            box-shadow: inset 1px 1px 0 rgba(255,255,255,0.35);
        }
        .xp-sysbtn.xp-close {
            background: linear-gradient(180deg, #e08080 0%, #c04040 50%, #902020 100%);
        }
        .xp-menubar {
            display: flex;
            gap: 14px;
            padding: 3px 8px 4px;
            background: var(--xp-face);
            border-bottom: 1px solid var(--xp-shadow);
            box-shadow: inset 0 1px 0 var(--xp-hilite);
            font-size: 11px;
        }
        .xp-menubar span {
            color: #000;
            cursor: default;
        }
        .xp-menubar span:first-letter { text-decoration: underline; }
        .xp-titlebar, .xp-menubar { flex-shrink: 0; }
        .xp-client {
            flex: 1;
            min-height: 0;
            padding: 8px 10px 10px;
            background: var(--xp-face);
            overflow-x: hidden;
            overflow-y: auto;
            -webkit-overflow-scrolling: touch;
        }
        .xp-group {
            margin: 0 0 8px;
            padding: 8px 8px 10px;
            border: 1px solid var(--xp-shadow);
            border-radius: 0;
            background: var(--xp-face);
            box-shadow:
                inset 1px 1px 0 var(--xp-hilite),
                inset -1px -1px 0 #b0b0b0;
        }
        .xp-group legend {
            padding: 0 6px;
            font-size: 11px;
            color: var(--xp-link);
        }
        .xp-center { text-align: center; }
        .xp-hint {
            font-size: 11px;
            color: #4a4a4a;
            margin: 4px 0 0;
            line-height: 1.35;
        }
        .xp-status-ok { color: var(--xp-green); }
        #colorWheel {
            margin: 4px auto;
            border: 2px solid;
            border-color: var(--xp-shadow) var(--xp-hilite) var(--xp-hilite) var(--xp-shadow);
            cursor: crosshair;
            display: block;
            background: #000;
        }
        #currentColor {
            width: 44px;
            height: 44px;
            margin: 6px auto;
            border: 2px solid;
            border-color: var(--xp-shadow) var(--xp-hilite) var(--xp-hilite) var(--xp-shadow);
            border-radius: 50%;
            background: #0f0;
        }
        .xp-btn-row {
            margin-top: 8px;
            display: flex;
            gap: 8px;
            justify-content: center;
            flex-wrap: wrap;
        }
        .xp-btn {
            min-width: 72px;
            padding: 4px 12px 5px;
            font-family: inherit;
            font-size: 11px;
            color: #000;
            background: linear-gradient(180deg, #fff 0%, var(--xp-face-mid) 88%, #c0bdb5 100%);
            border: 1px solid;
            border-color: var(--xp-hilite) var(--xp-deep) var(--xp-deep) var(--xp-hilite);
            box-shadow: inset 1px 1px 0 var(--xp-hilite);
            cursor: pointer;
        }
        .xp-btn:active {
            border-color: var(--xp-deep) var(--xp-hilite) var(--xp-hilite) var(--xp-deep);
            padding: 5px 11px 4px 13px;
        }
        .xp-btn-red { color: #fff; background: linear-gradient(180deg, #e86868 0%, #b02020 100%); border-color: #faa #400 #400 #faa; }
        .xp-btn-green { background: linear-gradient(180deg, #b8f0b0 0%, #58a850 100%); }
        .xp-btn-blue { color: #fff; background: linear-gradient(180deg, #6898e8 0%, #2040a0 100%); border-color: #acf #102060 #102060 #acf; }
        .onoff-btn {
            margin-top: 10px;
            min-width: 120px;
            padding: 4px 16px 5px;
            font-family: inherit;
            font-size: 11px;
            font-weight: bold;
            cursor: pointer;
            background: linear-gradient(180deg, #fff 0%, var(--xp-face-mid) 90%, #b8b5ad 100%);
            border: 1px solid;
            border-color: var(--xp-hilite) var(--xp-deep) var(--xp-deep) var(--xp-hilite);
            box-shadow: inset 1px 1px 0 var(--xp-hilite);
        }
        .onoff-btn.on { color: var(--xp-green); }
        .onoff-btn.off { color: var(--xp-red); }
        .onoff-btn:active {
            border-color: var(--xp-deep) var(--xp-hilite) var(--xp-hilite) var(--xp-deep);
        }
        .slider-row {
            margin-top: 8px;
            font-size: 11px;
        }
        .slider-row label { color: #000; }
        .slider-row input[type="range"] {
            width: min(200px, 88%);
            vertical-align: middle;
            accent-color: #245edc;
        }
        .xp-num {
            width: 4.2em;
            margin-left: 6px;
            padding: 2px 4px;
            font-family: "Tahoma", monospace;
            font-size: 11px;
            border: 1px solid #7f9db9;
            background: #fff;
        }
        .status-bar {
            margin-top: 10px;
            display: flex;
            justify-content: space-between;
            gap: 6px;
        }
        .status-item {
            flex: 1;
            padding: 5px 6px;
            font-size: 10px;
            border: 1px solid;
            border-color: var(--xp-shadow) var(--xp-hilite) var(--xp-hilite) var(--xp-shadow);
            background: #fff;
            box-shadow: inset 1px 1px 0 #aca899;
        }
        .eq-stereo {
            display: flex;
            justify-content: center;
            gap: 12px;
        }
        .eq-channel {
            display: flex;
            flex-direction: column;
            align-items: center;
        }
        .eq-channel-label {
            font-size: 11px;
            font-weight: bold;
            margin-bottom: 6px;
            color: #000;
        }
        .eq-sliders {
            display: flex;
            align-items: flex-end;
            justify-content: center;
            gap: 6px;
        }
        .eq-band-wrap {
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            width: 28px;
        }
        .eq-band-wrap label {
            font-size: 9px;
            margin-top: 4px;
            color: #333;
        }
        .eq-band-wrap input[type="range"] {
            width: 100px;
            height: 18px;
            margin: 41px 0 0 -36px;
            transform: rotate(-90deg);
            transform-origin: center center;
            display: block;
            accent-color: #245edc;
        }
        .eq-band-db { font-size: 10px; color: #404040; margin-top: 2px; }
        .xy-title { font-size: 11px; margin-bottom: 6px; color: #000; font-weight: normal; }
        .xp-canvas-frame {
            display: inline-block;
            margin: 0 auto;
            padding: 4px;
            border: 1px solid;
            border-color: var(--xp-shadow) var(--xp-hilite) var(--xp-hilite) var(--xp-shadow);
            background: #1a1a1a;
        }
        #xyOutCanvas {
            display: block;
            margin: 0;
            border: 1px solid #000;
            background: #0a0a0a;
        }
        .np-buttons { display: flex; flex-wrap: wrap; gap: 6px; justify-content: center; margin-top: 8px; }
        .btn-np { min-width: 64px; }
    </style>
</head>
<body>
    <div class="xp-desktop">
        <div class="xp-crt-43">
        <div class="xp-window">
            <div class="xp-titlebar">
                <span class="xp-titlebar-icon" aria-hidden="true"></span>
                <span class="xp-titlebar-text">Laser Control</span>
                <div class="xp-titlebar-ctrl">
                    <button type="button" class="xp-sysbtn" title="Minimize" disabled tabindex="-1">_</button>
                    <button type="button" class="xp-sysbtn" title="Maximize" disabled tabindex="-1">□</button>
                    <button type="button" class="xp-sysbtn xp-close" title="Close" disabled tabindex="-1">×</button>
                </div>
            </div>
            <div class="xp-menubar" aria-hidden="true">
                <span>File</span><span>Edit</span><span>View</span><span>Help</span>
            </div>
            <div class="xp-client">
                <fieldset class="xp-group xp-center">
                    <legend>Laser color</legend>
                    <canvas id="colorWheel" width="240" height="240"></canvas>
                    <div id="currentColor"></div>
                    <div class="xp-btn-row">
                        <button type="button" class="xp-btn xp-btn-red" onclick="sendRGB(255, 0, 0)">RED</button>
                        <button type="button" class="xp-btn xp-btn-green" onclick="sendRGB(0, 255, 0)">GREEN</button>
                        <button type="button" class="xp-btn xp-btn-blue" onclick="sendRGB(0, 0, 255)">BLUE</button>
                    </div>
                    <div class="slider-row">
                        <label for="brightnessSlider">Brightness</label><br>
                        <input id="brightnessSlider" type="range" min="0" max="100" value="100"
                               oninput="updateBrightness(this.value)">
                        <span id="brightnessValue">100</span>%
                    </div>
                    <button type="button" id="onoffButton" class="onoff-btn on" onclick="toggleLaser()">Laser ON</button>
                </fieldset>

                <fieldset class="xp-group">
                    <legend>System status</legend>
                    <div class="status-bar">
                        <div class="status-item" id="statusTemp">CPU: -- °C</div>
                        <div class="status-item" id="statusLoad">Load: -- %</div>
                        <div class="status-item" id="statusUptime">Up: --s</div>
                    </div>
                </fieldset>

                <fieldset class="xp-group">
                    <legend>USB audio — gain &amp; delay</legend>
                    <div class="slider-row xp-center">
                        <label for="gainDbSlider">Gain</label><br>
                        <input id="gainDbSlider" type="range" min="-18" max="18" step="0.5" value="0"
                               oninput="updateGainDb(this.value)">
                        <span id="gainDbValue">0</span> dB
                    </div>
                    <div class="delay-section slider-row xp-center">
                        <label for="delaySlider">Channel delay (R), s</label><br>
                        <input id="delaySlider" type="range" min="0" max="500" value="10" step="1"
                               oninput="updateDelayFromSlider(this.value)">
                        <input id="delayNumber" class="xp-num" type="number" min="0" max="0.5" step="0.001" value="0.01"
                               oninput="updateDelayFromNumber(this.value)">
                        <span id="delayUnit">s</span>
                    </div>
                </fieldset>

                <fieldset class="xp-group">
                    <legend>USB EQ (3-band stereo, dB)</legend>
                    <div class="eq-stereo">
                        <div class="eq-channel">
                            <div class="eq-channel-label">L</div>
                            <div class="eq-sliders" id="eqSlidersL"></div>
                        </div>
                        <div class="eq-channel">
                            <div class="eq-channel-label">R</div>
                            <div class="eq-sliders" id="eqSlidersR"></div>
                        </div>
                    </div>
                </fieldset>

                <fieldset class="xp-group xp-center">
                    <legend>Galvo XY</legend>
                    <p class="xp-hint" style="margin-top:0">X = R channel, Y = L channel (USB audio output)</p>
                    <div class="xp-canvas-frame">
                        <canvas id="xyOutCanvas" width="240" height="240"></canvas>
                    </div>
                    <div class="slider-row">
                        <label for="xyOutScaleSlider">Scale</label><br>
                        <input id="xyOutScaleSlider" type="range" min="25" max="200" value="100"
                               oninput="updateXYOutScale(this.value)">
                        <span id="xyOutScaleValue">100</span>%
                    </div>
                    <button type="button" id="xyVisualizerButton" class="onoff-btn off" onclick="toggleXYVisualizer()">XY visualizer OFF</button>
                </fieldset>

                <fieldset class="xp-group">
                    <legend>Now Playing — background shader</legend>
                    <p class="xp-hint">HDMI now-playing screen. Colors follow album art.</p>
                    <div class="np-buttons" id="npButtons">
                        <button type="button" class="btn-np xp-btn" data-preset="0">Waves</button>
                        <button type="button" class="btn-np xp-btn" data-preset="1">Plasma</button>
                        <button type="button" class="btn-np xp-btn" data-preset="2">Aurora</button>
                        <button type="button" class="btn-np xp-btn" data-preset="3">Ripple</button>
                        <button type="button" class="btn-np xp-btn" data-preset="4">Noise</button>
                    </div>
                    <p id="npStatus" class="xp-hint" style="min-height:14px;margin-top:8px"></p>
                </fieldset>
            </div>
        </div>
        </div>
    </div>

    <script>
        if ('serviceWorker' in navigator) {
            window.addEventListener('load', () => {
                navigator.serviceWorker.register('/sw.js').catch(console.error);
            });
        }

        const canvas = document.getElementById('colorWheel');
        const ctx = canvas.getContext('2d');
        const centerX = canvas.width / 2;
        const centerY = canvas.height / 2;
        const radius = Math.min(canvas.width, canvas.height) / 2 - 10;
        const innerKnobR = Math.round(Math.min(canvas.width, canvas.height) * 0.1);

        const imageCanvas = document.createElement('canvas');
        imageCanvas.width = canvas.width;
        imageCanvas.height = canvas.height;
        const imageCtx = imageCanvas.getContext('2d');

        // draw color wheel
        for (let angle = 0; angle < 360; angle++) {
            const startAngle = (angle - 1) * Math.PI / 180;
            const endAngle = angle * Math.PI / 180;
            imageCtx.beginPath();
            imageCtx.moveTo(centerX, centerY);
            imageCtx.arc(centerX, centerY, radius, startAngle, endAngle);
            imageCtx.closePath();
            imageCtx.fillStyle = `hsl(${angle}, 100%, 50%)`;
            imageCtx.fill();
        }
        imageCtx.beginPath();
        imageCtx.arc(centerX, centerY, innerKnobR, 0, 2 * Math.PI);
        imageCtx.fillStyle = '#ffffff';
        imageCtx.fill();
        ctx.drawImage(imageCanvas, 0, 0);

        function clamp255(v) { return Math.max(0, Math.min(255, v)); }

        function sendRGB(r, g, b) {
            fetch('/set_rgb', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ r, g, b })
            }).then(r => r.json()).then(d => {
                if (d.hex) {
                    document.getElementById('currentColor').style.backgroundColor = d.hex;
                }
            });
        }

        function handleCanvasEvent(e) {
            const rect = canvas.getBoundingClientRect();
            const clientX = e.clientX ?? (e.touches && e.touches[0].clientX);
            const clientY = e.clientY ?? (e.touches && e.touches[0].clientY);
            if (clientX == null || clientY == null) return;

            const sx = canvas.width / rect.width;
            const sy = canvas.height / rect.height;
            const x = (clientX - rect.left) * sx;
            const y = (clientY - rect.top) * sy;

            const dx = x - centerX;
            const dy = y - centerY;
            const distance = Math.sqrt(dx*dx + dy*dy);
            if (distance > radius) return;

            const pixel = imageCtx.getImageData(x, y, 1, 1).data;
            const r = clamp255(pixel[0]);
            const g = clamp255(pixel[1]);
            const b = clamp255(pixel[2]);

            document.getElementById('currentColor').style.backgroundColor =
                `rgb(${r},${g},${b})`;
            currentRgb = { r, g, b };
            sendRGB(r, g, b);
        }

        let isDragging = false;
        canvas.addEventListener('mousedown', e => {
            isDragging = true;
            handleCanvasEvent(e);
        });
        canvas.addEventListener('mousemove', e => {
            if (isDragging) handleCanvasEvent(e);
        });
        window.addEventListener('mouseup', () => { isDragging = false; });

        canvas.addEventListener('touchstart', e => {
            e.preventDefault();
            isDragging = true;
            handleCanvasEvent(e);
        }, { passive: false });
        canvas.addEventListener('touchmove', e => {
            e.preventDefault();
            if (isDragging) handleCanvasEvent(e);
        }, { passive: false });
        window.addEventListener('touchend', () => { isDragging = false; });

        function toggleLaser() {
            fetch('/toggle', { method: 'POST' })
              .then(r => r.json())
              .then(d => {
                  const btn = document.getElementById('onoffButton');
                  if (d.on) {
                      btn.classList.remove('off');
                      btn.classList.add('on');
                      btn.textContent = 'Laser ON';
                  } else {
                      btn.classList.remove('on');
                      btn.classList.add('off');
                      btn.textContent = 'Laser OFF';
                  }
              });
        }

        function updateBrightness(val) {
            document.getElementById('brightnessValue').innerText = val;
            fetch('/brightness', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ value: parseFloat(val) })
            });
        }

        function updateStatus() {
            fetch('/status')
              .then(r => r.json())
              .then(d => {
                  document.getElementById('statusTemp').textContent =
                      'CPU: ' + d.cpu_temp.toFixed(1) + ' °C';
                  document.getElementById('statusLoad').textContent =
                      'Load: ' + d.cpu_load.toFixed(0) + ' %';
                  document.getElementById('statusUptime').textContent =
                      'Up: ' + d.uptime + ' s';
              });
        }

        setInterval(updateStatus, 2000);
        window.addEventListener('load', updateStatus);

        const EQ_BAND_LABELS = ['60', '1k', '10k'];
        const NUM_EQ_BANDS = 3;
        const EQ_MIN = -24, EQ_MAX = 24;
        const DELAY_MIN = 0, DELAY_MAX = 0.5;

        function buildChannelSliders(containerId, channelKey, bands, onUpdate) {
            const container = document.getElementById(containerId);
            container.innerHTML = '';
            const vals = Array.from({ length: NUM_EQ_BANDS }, (_, i) => (bands[i] != null ? Number(bands[i]) : 0));
            vals.forEach((val, i) => {
                const wrap = document.createElement('div');
                wrap.className = 'eq-band-wrap';
                wrap.dataset.channel = channelKey;
                const input = document.createElement('input');
                input.type = 'range';
                input.min = EQ_MIN;
                input.max = EQ_MAX;
                input.step = 0.5;
                input.value = val;
                input.dataset.band = i;
                const label = document.createElement('label');
                label.textContent = EQ_BAND_LABELS[i] + ' Hz';
                const dbSpan = document.createElement('div');
                dbSpan.className = 'eq-band-db';
                dbSpan.textContent = val + ' dB';
                input.addEventListener('input', () => {
                    dbSpan.textContent = input.value + ' dB';
                    onUpdate();
                });
                wrap.appendChild(input);
                wrap.appendChild(label);
                wrap.appendChild(dbSpan);
                container.appendChild(wrap);
            });
        }

        function sendEQ() {
            const bands_l = Array.from({ length: NUM_EQ_BANDS }, (_, j) => {
                const el = document.querySelector('#eqSlidersL .eq-band-wrap input[data-band="' + j + '"]');
                return el ? parseFloat(el.value) : 0;
            });
            const bands_r = Array.from({ length: NUM_EQ_BANDS }, (_, j) => {
                const el = document.querySelector('#eqSlidersR .eq-band-wrap input[data-band="' + j + '"]');
                return el ? parseFloat(el.value) : 0;
            });
            fetch('/api/eq', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bands_l, bands_r })
            });
        }

        function updateGainDb(val) {
            const v = parseFloat(val);
            document.getElementById('gainDbValue').textContent = v;
            fetch('/api/eq', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ gain_db: v })
            });
        }

        function delayToSlider(sec) {
            return Math.round(Math.max(0, Math.min(0.5, sec)) * 1000);
        }
        function sliderToDelay(val) {
            return Math.max(0, Math.min(0.5, parseFloat(val) / 1000));
        }
        function setDelayUI(sec) {
            sec = Math.max(0, Math.min(0.5, sec));
            document.getElementById('delaySlider').value = delayToSlider(sec);
            document.getElementById('delayNumber').value = sec.toFixed(3);
        }
        function sendDelay(sec) {
            sec = Math.max(0, Math.min(0.5, parseFloat(sec)));
            setDelayUI(sec);
            fetch('/api/eq', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ delay_sec: sec })
            });
        }
        function updateDelayFromSlider(val) {
            const sec = sliderToDelay(val);
            document.getElementById('delayNumber').value = sec.toFixed(3);
            fetch('/api/eq', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ delay_sec: sec })
            });
        }
        function updateDelayFromNumber(val) {
            const sec = Math.max(0, Math.min(0.5, parseFloat(val) || 0));
            document.getElementById('delaySlider').value = delayToSlider(sec);
            fetch('/api/eq', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ delay_sec: sec })
            });
        }

        function loadEQ() {
            fetch('/api/eq').then(r => r.json()).then(d => {
                const gainDb = d.gain_db != null ? d.gain_db : 0;
                document.getElementById('gainDbSlider').value = gainDb;
                document.getElementById('gainDbValue').textContent = gainDb;
                const delaySec = d.delay_sec != null ? d.delay_sec : 0.01;
                setDelayUI(delaySec);
                const bands_l = (d.bands_l || [0,0,0]).slice(0, NUM_EQ_BANDS);
                const bands_r = (d.bands_r || [0,0,0]).slice(0, NUM_EQ_BANDS);
                while (bands_l.length < NUM_EQ_BANDS) bands_l.push(0);
                while (bands_r.length < NUM_EQ_BANDS) bands_r.push(0);
                buildChannelSliders('eqSlidersL', 'L', bands_l, sendEQ);
                buildChannelSliders('eqSlidersR', 'R', bands_r, sendEQ);
            }).catch(() => {
                document.getElementById('eqSlidersL').innerHTML = '<span class="xp-hint">EQ service not running</span>';
                document.getElementById('eqSlidersR').innerHTML = '';
            });
        }
        window.addEventListener('load', loadEQ);

        // Live XY plot: USB card OUTPUT (post-EQ) — stroke color matches laser color
        const xyOutCanvas = document.getElementById('xyOutCanvas');
        const xyOutCtx = xyOutCanvas.getContext('2d');
        const xyOutCenterX = xyOutCanvas.width / 2;
        const xyOutCenterY = xyOutCanvas.height / 2;
        const xyOutScaleBase = Math.min(xyOutCanvas.width, xyOutCanvas.height) * 0.45;
        let xyOutScaleMult = 1.0;
        let currentRgb = { r: 0, g: 255, b: 0 };

        function updateXYOutScale(val) {
            xyOutScaleMult = parseFloat(val) / 100;
            document.getElementById('xyOutScaleValue').innerText = val;
        }

        let xyOutIntervalId = null;

        function drawXYOut() {
            if (xyOutIntervalId === null) return;
            const scale = xyOutScaleBase * xyOutScaleMult;
            Promise.all([fetch('/api/xy_out').then(r => r.json()), fetch('/api/rgb').then(r => r.json())])
                .then(([data, rgb]) => {
                    if (xyOutIntervalId === null) return;
                    const points = Array.isArray(data && data.points) ? data.points : [];
                    currentRgb = { r: (rgb && rgb.r) != null ? rgb.r : 0, g: (rgb && rgb.g) != null ? rgb.g : 255, b: (rgb && rgb.b) != null ? rgb.b : 0 };
                    xyOutCtx.fillStyle = '#0a0a0a';
                    xyOutCtx.fillRect(0, 0, xyOutCanvas.width, xyOutCanvas.height);
                    xyOutCtx.strokeStyle = '#333';
                    xyOutCtx.lineWidth = 1;
                    xyOutCtx.beginPath();
                    xyOutCtx.moveTo(xyOutCenterX, 0);
                    xyOutCtx.lineTo(xyOutCenterX, xyOutCanvas.height);
                    xyOutCtx.moveTo(0, xyOutCenterY);
                    xyOutCtx.lineTo(xyOutCanvas.width, xyOutCenterY);
                    xyOutCtx.stroke();
                    if (points.length >= 2) {
                        xyOutCtx.strokeStyle = `rgb(${currentRgb.r}, ${currentRgb.g}, ${currentRgb.b})`;
                        xyOutCtx.lineWidth = 1.5;
                        xyOutCtx.beginPath();
                        for (let i = 0; i < points.length; i++) {
                            const px = Number(points[i][0]);
                            const py = Number(points[i][1]);
                            const x = xyOutCenterX + px * scale;
                            const y = xyOutCenterY - py * scale;
                            if (i === 0) xyOutCtx.moveTo(x, y);
                            else xyOutCtx.lineTo(x, y);
                        }
                        xyOutCtx.stroke();
                    }
                })
                .catch(() => {});
        }

        function toggleXYVisualizer() {
            const btn = document.getElementById('xyVisualizerButton');
            if (xyOutIntervalId !== null) {
                clearInterval(xyOutIntervalId);
                xyOutIntervalId = null;
                xyOutCtx.fillStyle = '#0a0a0a';
                xyOutCtx.fillRect(0, 0, xyOutCanvas.width, xyOutCanvas.height);
                xyOutCtx.fillStyle = '#808080';
                xyOutCtx.font = '11px Tahoma, sans-serif';
                xyOutCtx.textAlign = 'center';
                xyOutCtx.fillText('XY visualizer stopped', xyOutCenterX, xyOutCenterY);
                btn.classList.remove('on');
                btn.classList.add('off');
                btn.textContent = 'XY visualizer OFF';
            } else {
                xyOutCtx.fillStyle = '#0a0a0a';
                xyOutCtx.fillRect(0, 0, xyOutCanvas.width, xyOutCanvas.height);
                xyOutIntervalId = setInterval(drawXYOut, 16);
                drawXYOut();
                btn.classList.remove('off');
                btn.classList.add('on');
                btn.textContent = 'XY visualizer ON';
            }
        }

        fetch('/api/rgb').then(r => r.json()).then(d => {
            currentRgb = { r: d.r || 0, g: d.g || 255, b: d.b || 0 };
        }).catch(() => {});
        // XY visualizer off by default: show stopped state on canvas
        xyOutCtx.fillStyle = '#0a0a0a';
        xyOutCtx.fillRect(0, 0, xyOutCanvas.width, xyOutCanvas.height);
        xyOutCtx.fillStyle = '#808080';
        xyOutCtx.font = '11px Tahoma, sans-serif';
        xyOutCtx.textAlign = 'center';
        xyOutCtx.fillText('XY visualizer stopped', xyOutCenterX, xyOutCenterY);

        document.getElementById('npButtons').addEventListener('click', function(e) {
            const btn = e.target.closest('[data-preset]');
            if (!btn) return;
            const preset = btn.getAttribute('data-preset');
            const statusEl = document.getElementById('npStatus');
            statusEl.textContent = 'Setting...';
            fetch('/api/now-playing-background', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ preset: preset })
            }).then(function(r) { return r.json(); }).then(function(d) {
                statusEl.textContent = d.ok ? 'Set. HDMI screen will update in a moment.' : 'Now-playing server not reachable.';
                setTimeout(function() { statusEl.textContent = ''; }, 3000);
            }).catch(function() {
                statusEl.textContent = 'Request failed.';
                setTimeout(function() { statusEl.textContent = ''; }, 3000);
            });
        });
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    r = render_template_string(HTML)
    resp = app.make_response(r)
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@app.route('/api/rgb')
def api_rgb():
    """Current laser color as RGB 0-255 (for XY graph stroke)."""
    r = int(round((100.0 - base_color['r']) / 100.0 * 255))
    g = int(round((100.0 - base_color['g']) / 100.0 * 255))
    b = int(round((100.0 - base_color['b']) / 100.0 * 255))
    return jsonify({'r': max(0, min(255, r)), 'g': max(0, min(255, g)), 'b': max(0, min(255, b))})


@app.route('/set_rgb', methods=['POST'])
def set_rgb():
    data = request.get_json(force=True, silent=True) or {}
    r = data.get('r', 0)
    g = data.get('g', 0)
    b = data.get('b', 0)
    set_base_color_from_rgb8(r, g, b)
    hex_color = '#{0:02x}{1:02x}{2:02x}'.format(int(r), int(g), int(b))
    return jsonify({'status': 'ok', 'r': r, 'g': g, 'b': b, 'hex': hex_color})

@app.route('/toggle', methods=['POST'])
def toggle():
    global laser_on
    laser_on = not laser_on
    # Always call apply_color to ensure proper state
    apply_color()
    return jsonify({'status': 'ok', 'on': laser_on})

@app.route('/brightness', methods=['POST'])
def set_brightness():
    global brightness
    data = request.get_json(force=True, silent=True) or {}
    val = float(data.get('value', 100.0))
    brightness = max(0.0, min(100.0, val))
    apply_color()
    return jsonify({'status': 'ok', 'brightness': brightness})

@app.route('/status')
def status():
    return jsonify({
        'cpu_temp': get_cpu_temp_c(),
        'cpu_load': get_cpu_load_pct(),
        'uptime': get_uptime_sec(),
        'laser_on': laser_on,
        'gpio_available': GPIO_AVAILABLE,
        'brightness': brightness,
        'color': {
            'r': int(round((100.0 - base_color['r']) / 100.0 * 255)),
            'g': int(round((100.0 - base_color['g']) / 100.0 * 255)),
            'b': int(round((100.0 - base_color['b']) / 100.0 * 255)),
        }
    })


def _eq_request(method, data=None):
    try:
        req = urllib.request.Request(
            EQ_SERVICE_URL + "/eq",
            data=json.dumps(data).encode() if data else None,
            method=method,
            headers={"Content-Type": "application/json"} if data else {}
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None


def _xy_out_request():
    try:
        req = urllib.request.Request(EQ_SERVICE_URL + "/xy_out", method="GET")
        with urllib.request.urlopen(req, timeout=1) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return None


@app.route('/api/xy_out')
def api_xy_out():
    out = _xy_out_request()
    return jsonify(out if out else {"points": []})


def _now_playing_background_set(preset):
    try:
        req = urllib.request.Request(
            NOW_PLAYING_API + "/api/background",
            data=json.dumps({"preset": str(preset)}).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


@app.route("/now-playing-background/<preset>")
def now_playing_background(preset):
    _now_playing_background_set(preset)
    return ("<html><body><p>Background set to preset " + str(preset) + ".</p><p><a href='/'>Back to Laser Control</a></p></body></html>", 200, {"Content-Type": "text/html"})


@app.route("/api/now-playing-background", methods=["POST"])
def api_now_playing_background():
    """Set now-playing shader preset (no redirect)."""
    data = request.get_json(force=True, silent=True) or {}
    preset = str(data.get("preset", "0"))
    ok = _now_playing_background_set(preset) is not None
    return jsonify({"ok": ok, "preset": preset})


@app.route('/api/eq', methods=['GET', 'POST'])
def api_eq():
    if request.method == 'GET':
        out = _eq_request("GET")
        return jsonify(out if out else {"bands_l": [0] * 3, "bands_r": [0] * 3, "gain_db": 0, "delay_sec": 0.01})
    data = request.get_json(force=True, silent=True) or {}
    payload = {}
    if "gain_db" in data:
        payload["gain_db"] = data["gain_db"]
    if "delay_sec" in data:
        payload["delay_sec"] = data["delay_sec"]
    bands_l = data.get("bands_l", [])
    bands_r = data.get("bands_r", [])
    if len(bands_l) == 3 and len(bands_r) == 3:
        payload["bands_l"] = bands_l
        payload["bands_r"] = bands_r
    if not payload:
        return jsonify({"error": "send gain_db, delay_sec, and/or bands_l and bands_r (length 3 each)"}), 400
    out = _eq_request("POST", payload)
    return jsonify(out) if out else (jsonify({"error": "EQ service unavailable"}), 503)

@app.route('/manifest.json')
def manifest():
    return send_from_directory(STATIC_DIR, 'manifest.json')

@app.route('/sw.js')
def service_worker():
    return app.send_static_file('sw.js')

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=6969, debug=False)
    finally:
        if GPIO_AVAILABLE and lgpio_handle is not None:
            try:
                for _pin in (RED_PIN, GREEN_PIN, BLUE_PIN):
                    try:
                        lgpio.gpio_free(lgpio_handle, _pin)
                    except Exception:
                        pass
                    try:
                        lgpio.gpio_claim_output(lgpio_handle, _pin, 1)  # HIGH → transistor ON → laser OFF
                    except Exception:
                        pass
                for _pin in (RED_PIN, GREEN_PIN, BLUE_PIN):
                    try:
                        lgpio.gpio_free(lgpio_handle, _pin)
                    except Exception:
                        pass
                lgpio.gpiochip_close(lgpio_handle)
            except Exception:
                pass
