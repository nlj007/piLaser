from flask import Flask, request, jsonify, render_template_string
import lgpio

app = Flask(__name__)

# Current pin assignments and state
pins = {'r': 12, 'g': 16, 'b': 22}
state = {'r': False, 'g': False, 'b': False}

h = None

def gpio_init():
    global h
    if h is not None:
        try:
            lgpio.gpiochip_close(h)
        except:
            pass
    h = lgpio.gpiochip_open(0)
    for pin in pins.values():
        try:
            lgpio.gpio_free(h, pin)
        except:
            pass
        lgpio.gpio_claim_output(h, pin, 1)  # 1 = transistor ON = control 0V = laser OFF (active-low)

def apply_state():
    if h is None:
        return
    for color, pin in pins.items():
        try:
            lgpio.gpio_write(h, pin, 0 if state[color] else 1)  # active-low: 0=laser ON, 1=laser OFF
        except Exception as e:
            print(f"GPIO write error pin {pin}: {e}")

gpio_init()

HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>Laser GPIO Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: sans-serif; max-width: 480px; margin: 40px auto; padding: 0 20px; background: #111; color: #eee; }
        h1 { font-size: 1.4em; margin-bottom: 24px; }
        h2 { font-size: 1em; color: #aaa; margin: 24px 0 10px; }
        .row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
        label { width: 60px; font-weight: bold; }
        label.r { color: #f55; }
        label.g { color: #5f5; }
        label.b { color: #55f; }
        input[type=number] { width: 70px; padding: 6px; background: #222; color: #eee; border: 1px solid #444; border-radius: 4px; font-size: 1em; }
        button { padding: 8px 20px; border: none; border-radius: 4px; cursor: pointer; font-size: 1em; }
        .apply { background: #333; color: #eee; }
        .on  { background: #2a2; color: #fff; }
        .off { background: #444; color: #ccc; }
        .all-off { width: 100%; margin-top: 16px; background: #622; color: #fff; padding: 12px; }
        .status { font-size: 0.85em; color: #888; margin-top: 20px; }
    </style>
</head>
<body>
    <h1>Laser GPIO Control</h1>

    <h2>Pin Assignment</h2>
    <div class="row"><label class="r">RED</label>   <input type="number" id="pin_r" value="{{ pins.r }}"> GPIO</div>
    <div class="row"><label class="g">GREEN</label> <input type="number" id="pin_g" value="{{ pins.g }}"> GPIO</div>
    <div class="row"><label class="b">BLUE</label>  <input type="number" id="pin_b" value="{{ pins.b }}"> GPIO</div>
    <button class="apply" onclick="setPins()">Apply Pins</button>

    <h2>Control</h2>
    <div class="row">
        <label class="r">RED</label>
        <button id="btn_r" onclick="toggle('r')">{{ 'ON' if state.r else 'OFF' }}</button>
    </div>
    <div class="row">
        <label class="g">GREEN</label>
        <button id="btn_g" onclick="toggle('g')">{{ 'ON' if state.g else 'OFF' }}</button>
    </div>
    <div class="row">
        <label class="b">BLUE</label>
        <button id="btn_b" onclick="toggle('b')">{{ 'ON' if state.b else 'OFF' }}</button>
    </div>
    <button class="all-off" onclick="allOff()">ALL OFF</button>

    <div class="status" id="status"></div>

    <script>
        function updateButtons(s) {
            for (const c of ['r','g','b']) {
                const btn = document.getElementById('btn_' + c);
                btn.textContent = s[c] ? 'ON' : 'OFF';
                btn.className = s[c] ? 'on' : 'off';
            }
        }

        function setPins() {
            fetch('/set_pins', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    r: parseInt(document.getElementById('pin_r').value),
                    g: parseInt(document.getElementById('pin_g').value),
                    b: parseInt(document.getElementById('pin_b').value)
                })
            }).then(r => r.json()).then(d => {
                document.getElementById('status').textContent = d.ok ? 'Pins applied.' : 'Error: ' + d.error;
                updateButtons(d.state);
            });
        }

        function toggle(color) {
            fetch('/toggle', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({color})
            }).then(r => r.json()).then(d => updateButtons(d.state));
        }

        function allOff() {
            fetch('/all_off', {method: 'POST'})
                .then(r => r.json()).then(d => updateButtons(d.state));
        }

        updateButtons({{ state|tojson }});
    </script>
</body>
</html>
'''

@app.route('/')
def index():
    return render_template_string(HTML, pins=pins, state=state)

@app.route('/set_pins', methods=['POST'])
def set_pins():
    global h
    data = request.get_json()
    try:
        new_pins = {
            'r': int(data['r']),
            'g': int(data['g']),
            'b': int(data['b'])
        }
        # Turn everything off on old pins first
        for color, pin in pins.items():
            try:
                lgpio.gpio_write(h, pin, 0)
                lgpio.gpio_free(h, pin)
            except:
                pass
        pins.update(new_pins)
        state.update({'r': False, 'g': False, 'b': False})
        # Claim new pins
        for pin in pins.values():
            try:
                lgpio.gpio_free(h, pin)
            except:
                pass
            lgpio.gpio_claim_output(h, pin, 1)  # 1 = transistor ON = control 0V = laser OFF (active-low)
        return jsonify({'ok': True, 'state': state})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e), 'state': state})

@app.route('/toggle', methods=['POST'])
def toggle():
    color = request.get_json().get('color')
    if color in state:
        state[color] = not state[color]
        apply_state()
    return jsonify({'state': state})

@app.route('/all_off', methods=['POST'])
def all_off():
    for color in state:
        state[color] = False
    apply_state()
    return jsonify({'state': state})

if __name__ == '__main__':
    try:
        app.run(host='0.0.0.0', port=6980, debug=False)
    finally:
        for pin in pins.values():
            try:
                lgpio.gpio_write(h, pin, 0)
            except:
                pass
        if h:
            lgpio.gpiochip_close(h)
