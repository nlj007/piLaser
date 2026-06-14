# RGB Laser Wiring — Single Channel (×3 identical)

```mermaid
graph TD
    PWR(["⚡ 12V Supply (+)"])
    LASER["Laser Module\n(+) power in · (−) switched ground\n─────── beam output ───────"]
    NPN["NPN BJT  ─  2N2222 / BC547\nCOLLECTOR  │  BASE  │  EMITTER"]
    GND(["⏚ GND (−)"])

    GPIO(["Pi GPIO · 3.3V PWM\nduty 1.0 = ON · duty 0.0 = OFF"])
    R_B["1 kΩ  base resistor"]
    J((" "))
    R_PD["10 kΩ  pull-down"]

    PWR   -->|"+12V"| LASER
    LASER -->|"cathode → COLLECTOR"| NPN
    NPN   -->|"EMITTER"| GND

    GPIO  --> R_B --> J
    J     -->|"→ BASE"| NPN
    J     --> R_PD --> GND
```

**Pins:** GPIO 12 = RED (Pin 32) · GPIO 16 = GREEN (Pin 36) · GPIO 21 = BLUE (Pin 40) · GND = Pin 39

**⚠️ ACTIVE-LOW laser module:** control line 0V = color OFF · control line ~2.5V (floating) = color ON
- GPIO HIGH → transistor ON → control = 0V → **laser color OFF**
- GPIO LOW → transistor OFF → control = ~2.5V → **laser color ON**
- PWM duty in software is therefore **inverted**: duty=0.0 = full brightness, duty=1.0 = off
