# RGB Laser GPIO — Confirmed Working Config (2026-06-15)

## Hardware

### Transistor circuit (×3 identical channels)
- NPN BJT (2N2222 / BC547) in common-emitter low-side switch
- Base resistor: 1 kΩ (GPIO → base)
- Pull-down: 10 kΩ (base → GND) — prevents floating base at boot
- Collector → laser module control input
- Emitter → GND

### Pin assignments (Raspberry Pi 5)
| Color | GPIO | Physical Pin |
|-------|------|--------------|
| RED   | 12   | 32           |
| GREEN | 16   | 36           |
| BLUE  | 21   | 40           |
| GND   | —    | 39           |

**Do NOT use:** GPIO 20 (Pi 5 power button), GPIO 4/17 (shorted on old board).

### Wiring diagrams
- `laser_wiring_simple.md` — single-channel Mermaid diagram
- `laser_wiring.md` — full 3-channel diagram

---

## CRITICAL: Laser module is ACTIVE-LOW

Discovered empirically: with all three channels driven HIGH (transistor ON), the laser output is OFF.

| GPIO level | Transistor | Control line voltage | Laser color |
|------------|------------|----------------------|-------------|
| HIGH (1)   | ON (sat.)  | ~0 V                 | **OFF**     |
| LOW  (0)   | OFF        | ~2.5 V (floating)    | **ON**      |

---

## CRITICAL: Use `gpio_free` + `gpio_claim_output`, not `tx_pwm` at extreme duty cycles

`lgpio.tx_pwm()` at duty=1.0 does NOT produce a clean DC HIGH. The Pi's software PWM still generates edges, so the transistor never fully saturates — Vce stays ~0.8 V instead of near 0 V, which is right at the TTL low threshold. Result: all channels appear "on" even when supposed to be off.

**Working approach:**
- Fully off (channel = 0% brightness): `gpio_free(pin)` → `gpio_claim_output(pin, 1)` — clean DC HIGH
- Fully on (channel = 100% brightness): `gpio_free(pin)` → `gpio_claim_output(pin, 0)` — clean DC LOW
- Intermediate brightness: `tx_pwm(pin, FREQ, duty)` where `duty = 1.0 - bval/100.0` (inverted for active-low)

This is the same principle as `laser_gpio_control.py` (port 6980), which uses plain `gpio_write` and was the confirmed-working reference app throughout debugging.

**Why `gpio_free` before `gpio_claim_output`:** after `tx_pwm` is running on a pin, `gpio_write` is overridden by the TX system. `gpio_free` stops any active TX and releases the claim; `gpio_claim_output` then reclaims it with a clean static level.

---

## Key code patterns in `master-laser.py`

### Init (safe state — laser OFF)
```python
for _pin in (RED_PIN, GREEN_PIN, BLUE_PIN):
    lgpio.gpio_free(lgpio_handle, _pin)          # release any prior claim
    lgpio.gpio_claim_output(lgpio_handle, _pin, 1)  # HIGH → transistor ON → laser OFF
```

### `_pin_write` helper
```python
def _pin_write(pin, level):
    try:
        lgpio.gpio_free(lgpio_handle, pin)
    except Exception:
        pass
    lgpio.gpio_claim_output(lgpio_handle, pin, level)
```

### `apply_color` (abridged)
```python
def apply_color():
    if not laser_on:
        for p in (RED_PIN, GREEN_PIN, BLUE_PIN):
            _pin_write(p, 1)   # all OFF
        return
    scale = brightness / 100.0
    for pin, base in ((RED_PIN, base_color['r']), ...):
        bval = (100.0 - base) * scale   # 0=off, 100=full brightness
        if bval <= 0.0:
            _pin_write(pin, 1)           # fully off
        elif bval >= 100.0:
            _pin_write(pin, 0)           # fully on
        else:
            lgpio.tx_pwm(lgpio_handle, pin, FREQ, 1.0 - bval / 100.0)
```

### Internal color representation
`base_color` stores 0–100 where **0 = full intensity, 100 = off** (inverted from RGB).  
`set_base_color_from_rgb8(r, g, b)` converts: `base_color['r'] = 100 - (r/255)*100`.

---

## Service

```bash
sudo systemctl stop laser-web.service   # always stop before start (restart has a pin-busy race)
sudo systemctl start laser-web.service
sudo journalctl -u laser-web.service -n 30
```

Port: **6969**. UI accessible at `http://<pi-ip>:6969`.

---

## Debugging commands

```bash
# Check GPIO is claimed successfully
curl -s http://localhost:6969/status | python3 -m json.tool   # gpio_available must be true

# Set red via API
curl -s -X POST http://localhost:6969/set_rgb \
  -H "Content-Type: application/json" -d '{"r":255,"g":0,"b":0}'

# Measure collector voltage with multimeter:
# ~0 V (or < 0.4 V) when channel OFF = transistor saturating correctly
# ~2.5 V when channel ON = transistor off, laser module pull-up visible
```

---

## Debugging history (lessons learned)

1. **All LEDs lit simultaneously** — LEDs wired backwards (anode to GND). Fixed: flip LED so long leg → GPIO, short leg → GND.
2. **GND wired to GPIO 22 (Pin 15) not real GND (Pin 39)** — removing one LED broke others. Fixed: move GND wire to Pin 39.
3. **RED/GREEN pin labels swapped** — discovered via individual pin tests. Corrected in code.
4. **GPIO 27 (Pin 13) unreliable** — physical pin had contact issue. Moved BLUE to GPIO 21 (Pin 40).
5. **Active-low discovery** — "all three ON → laser off; turn off RED → laser shows red." Inverted all logic.
6. **PWM duty=1.0 leaves Vce at 0.8 V** — transistor doesn't fully saturate through software PWM. Fixed by using `gpio_free` + `gpio_claim_output` for fully-on/off states.
7. **`lgpio.tx_off` does not exist** — caused `apply_color` to crash silently on first pin, leaving laser stuck. Replaced with `gpio_free`.
