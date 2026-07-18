# Smart Water Tank Monitoring & Control System (IFT 211)

Individual submission for the IFT 211 mid-semester lab assessment: a digital
water-tank controller built from **pure digital logic** (gates + D flip-flops,
no microcontrollers, no abstract blocks) and simulated in **Logisim**.

## What it does

- Encodes four thermometer-coded level sensors (S0 Empty, S1 25 %, S2 50 %,
  S3 Full) into a 2-bit level code — `L1 = S2`, `L0 = S1·S̄2 + S3`.
- Detects FULL with a comparator (`FULL = L1·L0`) and stops the pump
  instantly (`PUMP = NAND(L1, L0)`).
- Stores the last valid level in a 2-flip-flop register (CLOCK-synchronised,
  RESET-cleared) and shows it as a digit 0–3 on a 7-segment display through a
  gate-built BCD decoder.
- Counts consecutive FULL clock cycles with a 2-bit synchronous counter and
  raises `ALARM = Q1·Q0` when FULL has persisted for 3 cycles.

## Folder map

| Path | Contents |
|---|---|
| `circuit/water_tank.circ` | The circuit — open in Logisim Evolution or classic Logisim 2.7.1 |
| `docs/REPORT.md` | Full submission report: derivations, truth tables, K-maps, test scenarios, explanation |
| `docs/REPORT.docx` | Same report as a Word document |
| `verification/verification_log.md` | Machine-generated simulation log: 71 steps, 0 mismatches |
| `verification/TableDriver.java` | Headless test-bench driver (drives the real circuit through the Logisim engine) |
| `verification/verify.py` | Reference model + vector generator + comparator |
| `verification/wt_vectors.txt` | The exact test vectors used |
| `verification/gen_circuit.py` | Script that generated `water_tank.circ` (geometry + wiring checks) |

## Quick start

1. Get Logisim: either **Logisim Evolution** (GitHub releases) or classic
   **Logisim 2.7.1** (`logisim-generic-2.7.1.jar`, SourceForge — run with
   `java -jar logisim-generic-2.7.1.jar`).
2. Open `circuit/water_tank.circ`.
3. With the **Poke tool**: set a thermometer code on `S0…S3` (50 % ⇒
   S0=S1=S2=1), then click `CLOCK` 0→1→0 for one clock cycle. Watch the
   7-segment digit, the `PUMP_ON` LED, and — after three clock cycles at
   full — the `ALARM_LED`. `RESET` clears register and counter.

## Re-run the automated verification

```bash
cd verification
# download logisim-generic-2.7.1.jar and save it here as logisim.jar
javac -cp logisim.jar TableDriver.java
python3 verify.py ../circuit/water_tank.circ
```

Expected output: `71 steps, 0 mismatches` and a regenerated
`verification_log.md`. The vectors cover an exhaustive sweep of all 16 sensor
codes (valid + invalid) plus the full operational scenario: fill 0→25→50→full,
pump ON→OFF→ON, the 3-clock-cycle alarm sequence, counter saturation,
asynchronous RESET, and invalid sensor patterns.
