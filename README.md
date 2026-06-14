# piLaser

RGB laser head controller for Raspberry Pi 5. Flask web app with color wheel UI, brightness control, and PWM dimming via GPIO.

## Hardware

NPN BJT (2N2222 / BC547) common-emitter low-side switch — one per color channel.

| Color | GPIO | Pi 5 Pin |
|-------|------|----------|
| RED   | 12   | 32       |
| GREEN | 16   | 36       |
| BLUE  | 21   | 40       |
| GND   | —    | 39       |

Per channel: 1 kΩ base resistor (GPIO → base), 10 kΩ pull-down (base → GND).

See `laser_wiring_simple.md` for the circuit diagram.

**⚠️ Laser module is ACTIVE-LOW:** control line 0 V = color OFF · floating ~2.5 V = color ON.

## Software

- `master-laser.py` — main Flask app (port 6969), XP Luna–themed UI
- `laser_gpio_control.py` — minimal GPIO test/reference app (port 6980)
- `laser-web.service` — systemd unit file
- `LASER_COLOR_WHEEL_FIX.md` — full debugging history and confirmed-working patterns

## Quick start

```bash
# Install dependencies (lgpio, flask)
pip install lgpio flask

# Run directly
python master-laser.py

# Or install as a service
sudo cp laser-web.service /etc/systemd/system/
sudo systemctl enable --now laser-web.service
```

Access the UI at `http://<pi-ip>:6969`.

## Key implementation notes

`tx_pwm` at duty=1.0 does **not** produce a clean DC HIGH on Pi 5 software PWM — the transistor won't fully saturate and Vce sits at ~0.8 V. For fully-on/off channels, use `gpio_free` + `gpio_claim_output` instead. See `LASER_COLOR_WHEEL_FIX.md` for the full explanation.
