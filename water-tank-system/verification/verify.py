#!/usr/bin/env python3
"""Verify water_tank.circ against a Python reference model.

Generates test vectors (exhaustive sensor sweep + realistic scenarios), runs
them through the real Logisim circuit via TableDriver (headless), and diffs
every output bit against the reference model. Exit code 0 = perfect match.
"""
import subprocess, sys, os

IN_NAMES = ["S3", "S2", "S1", "S0", "RESET", "CLOCK"]
OUT_NAMES = ["L1", "L0", "FULL", "PUMP", "R1", "R0", "CNT1", "CNT0", "ALARM",
             "SEG_A", "SEG_B", "SEG_C", "SEG_D", "SEG_E", "SEG_F", "SEG_G"]

# ---------------- reference model ----------------
class Model:
    def __init__(self):
        self.R1 = self.R0 = self.Q1 = self.Q0 = 0
        self.prev_clock = 0

    def step(self, S3, S2, S1, S0, RESET, CLOCK):
        L1 = S2
        L0 = int((S1 and not S2) or S3)
        FULL = int(L1 and L0)
        PUMP = int(not FULL)
        if CLOCK and not self.prev_clock:          # rising edge
            nR1, nR0 = L1, L0
            nQ1 = int(FULL and (self.Q1 or self.Q0))
            nQ0 = int(FULL and (self.Q1 or not self.Q0))
            self.R1, self.R0, self.Q1, self.Q0 = nR1, nR0, nQ1, nQ0
        if RESET:                                   # async clear dominates
            self.R1 = self.R0 = self.Q1 = self.Q0 = 0
        self.prev_clock = CLOCK
        ALARM = int(self.Q1 and self.Q0)
        a = d = int(self.R1 or not self.R0)
        b = 1
        c = int((not self.R1) or self.R0)
        e = int(not self.R0)
        f = int(not (self.R1 or self.R0))
        g = self.R1
        return dict(L1=L1, L0=L0, FULL=FULL, PUMP=PUMP,
                    R1=self.R1, R0=self.R0, CNT1=self.Q1, CNT0=self.Q0,
                    ALARM=ALARM, SEG_A=a, SEG_B=b, SEG_C=c, SEG_D=d,
                    SEG_E=e, SEG_F=f, SEG_G=g)

# ---------------- vector construction ----------------
vectors = []   # (S3,S2,S1,S0,RESET,CLOCK, comment)

def v(s3, s2, s1, s0, rst, ck, comment=""):
    vectors.append((s3, s2, s1, s0, rst, ck, comment))

# Part A - exhaustive sensor sweep, clocked (covers valid + all invalid codes)
v(0, 0, 0, 0, 1, 0, "A: global reset")
v(0, 0, 0, 0, 0, 0, "A: release reset")
for code in range(16):
    s3, s2, s1, s0 = (code >> 3) & 1, (code >> 2) & 1, (code >> 1) & 1, code & 1
    v(s3, s2, s1, s0, 0, 0, f"A: sensors={s3}{s2}{s1}{s0} setup")
    v(s3, s2, s1, s0, 0, 1, f"A: sensors={s3}{s2}{s1}{s0} clock edge")
v(0, 0, 0, 0, 0, 0, "A: idle low")

# Part B - realistic scenario
v(0, 0, 0, 0, 1, 0, "B1: RESET asserted, tank empty")
v(0, 0, 0, 0, 1, 1, "B1: clock during RESET (register must stay 00)")
v(0, 0, 0, 0, 1, 0, "B1: clock low")
v(0, 0, 0, 0, 0, 0, "B2: RESET released, tank empty (0000)")
v(0, 0, 0, 0, 0, 1, "B2: clock -> store level 00, display 0, pump ON")
v(0, 0, 0, 1, 0, 0, "B3: water reaches S0 only (0001) - still level 00")
v(0, 0, 0, 1, 0, 1, "B3: clock -> store 00")
v(0, 0, 1, 1, 0, 0, "B4: 25 percent (0011) - level 01")
v(0, 0, 1, 1, 0, 1, "B4: clock -> store 01, display 1")
v(0, 1, 1, 1, 0, 0, "B5: 50 percent (0111) - level 10")
v(0, 1, 1, 1, 0, 1, "B5: clock -> store 10, display 2")
v(1, 1, 1, 1, 0, 0, "B6: FULL (1111) - level 11, pump OFF")
v(1, 1, 1, 1, 0, 1, "B6: clock 1 of FULL -> counter 01")
v(1, 1, 1, 1, 0, 0, "B7: FULL held")
v(1, 1, 1, 1, 0, 1, "B7: clock 2 of FULL -> counter 10")
v(1, 1, 1, 1, 0, 0, "B8: FULL held")
v(1, 1, 1, 1, 0, 1, "B8: clock 3 of FULL -> counter 11, ALARM ON")
v(1, 1, 1, 1, 0, 0, "B9: FULL still held")
v(1, 1, 1, 1, 0, 1, "B9: clock 4 -> counter saturates 11, ALARM stays ON")
v(0, 1, 1, 1, 0, 0, "B10: drained to 50 percent - FULL=0, pump ON again")
v(0, 1, 1, 1, 0, 1, "B10: clock -> counter clears to 00, ALARM OFF, store 10")
v(0, 0, 1, 1, 0, 0, "B11: drained to 25 percent")
v(0, 0, 1, 1, 0, 1, "B11: clock -> store 01, display 1")
v(1, 1, 1, 1, 0, 0, "B12: refill to FULL")
v(1, 1, 1, 1, 0, 1, "B12: clock 1 of FULL -> counter 01")
v(1, 1, 1, 1, 0, 0, "B12: FULL held")
v(1, 1, 1, 1, 0, 1, "B12: clock 2 of FULL -> counter 10")
v(1, 1, 1, 1, 1, 0, "B13: operator RESET while FULL - counter clears NOW (async)")
v(1, 1, 1, 1, 0, 0, "B13: RESET released, FULL persists")
v(1, 1, 1, 1, 0, 1, "B13: clock -> counter restarts at 01")
v(0, 1, 0, 0, 0, 0, "B14: invalid code 0100 (S2 alone) - reads as level 10")
v(0, 1, 0, 0, 0, 1, "B14: clock -> store 10, display 2")
v(1, 0, 0, 0, 0, 0, "B15: invalid code 1000 (S3 alone) - reads as level 01")
v(1, 0, 0, 0, 0, 1, "B15: clock -> store 01, display 1")
v(0, 0, 0, 0, 1, 0, "B16: final reset")
v(0, 0, 0, 0, 0, 0, "B16: idle - system back to level 00")

# ---------------- write vector file ----------------
here = os.path.dirname(os.path.abspath(__file__))
vec_path = os.path.join(here, "wt_vectors.txt")
with open(vec_path, "w") as f:
    f.write("inputs: " + " ".join(IN_NAMES) + "\n")
    f.write("outputs: " + " ".join(OUT_NAMES) + "\n")
    for s3, s2, s1, s0, rst, ck, comment in vectors:
        f.write(f"{s3} {s2} {s1} {s0} {rst} {ck}  # {comment}\n")

# ---------------- run Logisim ----------------
circ = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "water_tank.circ")
cp = f"{os.path.join(here,'logisim.jar')}:{here}"
res = subprocess.run(["java", "-cp", cp, "-Djava.awt.headless=true",
                      "TableDriver", circ, vec_path],
                     capture_output=True, text=True, timeout=300)
rows = []
for line in res.stdout.splitlines():
    if "|" not in line or line.startswith("STEP"):
        continue
    _, inp, rest = line.split("|", 2)
    outbits = rest.split("#")[0].split()
    rows.append(outbits)
if len(rows) != len(vectors):
    print(res.stdout)
    print(res.stderr[-2000:] if res.stderr else "")
    sys.exit(f"expected {len(vectors)} rows, got {len(rows)}")

# ---------------- compare ----------------
model = Model()
mismatches = 0
report_rows = []
for i, (s3, s2, s1, s0, rst, ck, comment) in enumerate(vectors):
    exp = model.step(s3, s2, s1, s0, rst, ck)
    exp_bits = [str(exp[n]) for n in OUT_NAMES]
    got_bits = rows[i]
    ok = exp_bits == got_bits
    if not ok:
        mismatches += 1
        diffs = [f"{n}:exp{e}/got{g}" for n, e, g in zip(OUT_NAMES, exp_bits, got_bits) if e != g]
        print(f"MISMATCH step {i} ({comment}): {', '.join(diffs)}")
    report_rows.append((i, (s3, s2, s1, s0, rst, ck), exp_bits, got_bits, ok, comment))

print(f"\n{len(vectors)} steps, {mismatches} mismatches")

# ---------------- write log ----------------
log_path = sys.argv[2] if len(sys.argv) > 2 else os.path.join(here, "verification_log.md")
with open(log_path, "w") as f:
    f.write("# Verification log - water_tank.circ vs reference model\n\n")
    f.write(f"Simulator: Logisim 2.7.1 (headless, real circuit file). Steps: {len(vectors)}. Mismatches: {mismatches}.\n\n")
    f.write("| Step | S3 S2 S1 S0 | RESET | CLOCK | " + " | ".join(OUT_NAMES) + " | Match | Scenario |\n")
    f.write("|---" * (len(OUT_NAMES) + 6) + "|\n")
    for i, ins, exp_bits, got_bits, ok, comment in report_rows:
        s3, s2, s1, s0, rst, ck = ins
        f.write(f"| {i} | {s3} {s2} {s1} {s0} | {rst} | {ck} | " + " | ".join(got_bits)
                + f" | {'OK' if ok else 'FAIL'} | {comment} |\n")
print(f"wrote {log_path}")
sys.exit(0 if mismatches == 0 else 1)
