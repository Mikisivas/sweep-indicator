#!/usr/bin/env python3
"""Generate water_tank.circ - IFT 211 Smart Water Tank Monitoring & Control System.

Logisim 2.7.1 file format (also opens in Logisim Evolution).
Port geometry verified empirically against logisim-2.7.1.jar:
  gates anchor at output; AND/OR size30 inputs at (-30,+-10); NAND/NOR size30 at (-40,+-10)
  NOT size30 input at (-30,0)
  D-FF: loc=Q; CLK=(-40,0) D=(-40,20) Q=(0,0) NQ=(0,20) SET=(-30,30) CLR=(-10,30)
  7-seg: north g(0,0) f(10,0) a(20,0) b(30,0); south e(0,60) d(10,60) c(20,60) dp(30,60)
"""

comps = []   # (lib, name, (x,y), {attr:val})
wires = []   # ((x1,y1),(x2,y2))
ports = []   # (x,y) electrical connection points of components

def comp(lib, name, loc, attrs=None, port_offsets=((0, 0),)):
    comps.append((lib, name, loc, attrs or {}))
    for dx, dy in port_offsets:
        ports.append((loc[0] + dx, loc[1] + dy))

def wire(*pts):
    for a, b in zip(pts, pts[1:]):
        assert a[0] == b[0] or a[1] == b[1], f"diagonal wire {a}-{b}"
        assert a != b, f"zero wire {a}"
        wires.append((a, b))

# ---- component helpers (port offsets from empirical probe) ----
GATE2_30 = ((0, 0), (-30, -10), (-30, 10))      # AND/OR size 30, 2 inputs
NGATE2_30 = ((0, 0), (-40, -10), (-40, 10))     # NAND/NOR size 30, 2 inputs
NOT_30 = ((0, 0), (-30, 0))
FF = ((-40, 0), (-40, 20), (0, 0), (0, 20), (-30, 30), (-20, 30), (-10, 30))
SEG7 = ((0, 0), (10, 0), (20, 0), (30, 0), (0, 60), (10, 60), (20, 60), (30, 60))

def in_pin(loc, label):
    comp("0", "Pin", loc, {"tristate": "false", "label": label})

def out_pin(loc, label):
    comp("0", "Pin", loc, {"output": "true", "label": label})

def tunnel(loc, label):
    comp("0", "Tunnel", loc, {"label": label})

def led(loc, label):
    comp("5", "LED", loc, {"label": label})

def text(loc, s):
    comps.append(("6", "Text", loc, {"text": s}))

# ================= INPUTS =================
text((140, 50), "IFT 211 MID-SEMESTER LAB - SMART WATER TANK MONITORING AND CONTROL SYSTEM")
text((140, 70), "Individual submission - pure digital logic, Logisim 2.7.1 format")
in_pin((140, 100), "S3"); wire((140, 100), (200, 100)); tunnel((200, 100), "S3")
in_pin((140, 160), "S2"); wire((140, 160), (200, 160)); tunnel((200, 160), "S2")
in_pin((140, 220), "S1"); wire((140, 220), (200, 220)); tunnel((200, 220), "S1")
in_pin((140, 280), "S0"); wire((140, 280), (200, 280)); led((200, 280), "S0_WATER")
in_pin((140, 340), "RESET"); wire((140, 340), (200, 340)); tunnel((200, 340), "RST")
in_pin((140, 400), "CLOCK"); wire((140, 400), (200, 400)); tunnel((200, 400), "CLK")
text((140, 440), "S0 empty-level probe: redundant in minimized logic (see K-maps); shown on LED")

# ================= STEP 1: ENCODER =================
text((250, 90), "STEP 1 - SENSOR ENCODING: L1 = S2 ; L0 = S1 AND NOT S2 OR S3")
# L1 = S2 (tunnel-to-tunnel bridge)
tunnel((250, 130), "S2"); wire((250, 130), (290, 130)); tunnel((290, 130), "L1")
# NOT S2
tunnel((250, 200), "S2"); wire((250, 200), (280, 200))
comp("1", "NOT Gate", (310, 200), {"size": "30"}, NOT_30)
# AND: S1 * /S2
wire((310, 200), (330, 200))
tunnel((310, 220), "S1"); wire((310, 220), (330, 220))
comp("1", "AND Gate", (360, 210), {"size": "30", "inputs": "2"}, GATE2_30)
# OR: (S1*/S2) + S3
wire((360, 210), (380, 210), (380, 230), (400, 230))
tunnel((380, 250), "S3"); wire((380, 250), (400, 250))
comp("1", "OR Gate", (430, 240), {"size": "30", "inputs": "2"}, GATE2_30)
wire((430, 240), (460, 240)); tunnel((460, 240), "L0")
# level code output pins
tunnel((520, 130), "L1"); wire((520, 130), (560, 130)); out_pin((560, 130), "L1")
tunnel((520, 160), "L0"); wire((520, 160), (560, 160)); out_pin((560, 160), "L0")

# ================= STEP 2: COMPARATOR =================
text((630, 250), "STEP 2 - COMPARATOR: FULL = L1 AND L0 (level == 11)")
tunnel((640, 310), "L1"); wire((640, 310), (670, 310))
tunnel((640, 330), "L0"); wire((640, 330), (670, 330))
comp("1", "AND Gate", (700, 320), {"size": "30", "inputs": "2"}, GATE2_30)
wire((700, 320), (730, 320)); tunnel((730, 320), "FULL")
tunnel((700, 280), "FULL"); wire((700, 280), (740, 280)); out_pin((740, 280), "FULL")

# ================= STEP 3: PUMP =================
text((630, 355), "STEP 3 - PUMP CONTROL: PUMP = NAND(L1, L0) = NOT FULL")
tunnel((630, 370), "L1"); wire((630, 370), (660, 370))
tunnel((630, 390), "L0"); wire((630, 390), (660, 390))
comp("1", "NAND Gate", (700, 380), {"size": "30", "inputs": "2"}, NGATE2_30)
wire((700, 380), (730, 380)); wire((730, 380), (760, 380)); out_pin((760, 380), "PUMP")
wire((730, 380), (730, 420)); led((730, 420), "PUMP_ON")

# ================= STEP 4: REGISTER =================
text((650, 435), "STEP 4 - LEVEL REGISTER: two D flip-flops, clocked, async RESET")
# FF_R1 stores L1
tunnel((690, 460), "CLK"); wire((690, 460), (720, 460))
tunnel((690, 480), "L1"); wire((690, 480), (720, 480))
comp("4", "D Flip-Flop", (760, 460), None, FF)
wire((760, 460), (790, 460)); tunnel((790, 460), "R1")
wire((750, 490), (750, 530)); tunnel((750, 530), "RST")
# FF_R0 stores L0
tunnel((690, 560), "CLK"); wire((690, 560), (720, 560))
tunnel((690, 580), "L0"); wire((690, 580), (720, 580))
comp("4", "D Flip-Flop", (760, 560), None, FF)
wire((760, 560), (790, 560)); tunnel((790, 560), "R0")
wire((750, 590), (750, 630)); tunnel((750, 630), "RST")
# registered level output pins
tunnel((860, 460), "R1"); wire((860, 460), (900, 460)); out_pin((900, 460), "R1")
tunnel((860, 560), "R0"); wire((860, 560), (900, 560)); out_pin((900, 560), "R0")

# ================= STEP 5: OVERFLOW COUNTER + ALARM =================
text((650, 620), "STEP 5 - OVERFLOW COUNTER: counts while FULL=1, saturates at 3; ALARM = Q1 AND Q0")
# D1 = FULL * (Q1 + Q0)
tunnel((700, 640), "Q1"); wire((700, 640), (730, 640))
tunnel((700, 660), "Q0"); wire((700, 660), (730, 660))
comp("1", "OR Gate", (760, 650), {"size": "30", "inputs": "2"}, GATE2_30)
wire((760, 650), (810, 650))
tunnel((780, 670), "FULL"); wire((780, 670), (810, 670))
comp("1", "AND Gate", (840, 660), {"size": "30", "inputs": "2"}, GATE2_30)
# FF_Q1
wire((840, 660), (900, 660), (900, 670), (920, 670))
tunnel((880, 650), "CLK"); wire((880, 650), (920, 650))
comp("4", "D Flip-Flop", (960, 650), None, FF)
wire((960, 650), (990, 650)); tunnel((990, 650), "Q1")
wire((950, 680), (950, 710)); tunnel((950, 710), "RST")
# D0 = FULL * (Q1 + /Q0)
tunnel((700, 750), "NQ0"); wire((700, 750), (730, 750))
tunnel((700, 770), "Q1"); wire((700, 770), (730, 770))
comp("1", "OR Gate", (760, 760), {"size": "30", "inputs": "2"}, GATE2_30)
wire((760, 760), (810, 760))
tunnel((780, 780), "FULL"); wire((780, 780), (810, 780))
comp("1", "AND Gate", (840, 770), {"size": "30", "inputs": "2"}, GATE2_30)
# FF_Q0
wire((840, 770), (900, 770), (900, 780), (920, 780))
tunnel((880, 760), "CLK"); wire((880, 760), (920, 760))
comp("4", "D Flip-Flop", (960, 760), None, FF)
wire((960, 760), (990, 760)); tunnel((990, 760), "Q0")
wire((960, 780), (990, 780)); tunnel((990, 780), "NQ0")
wire((950, 790), (950, 820)); tunnel((950, 820), "RST")
# ALARM = Q1 * Q0
tunnel((1020, 690), "Q1"); wire((1020, 690), (1050, 690))
tunnel((1020, 710), "Q0"); wire((1020, 710), (1050, 710))
comp("1", "AND Gate", (1080, 700), {"size": "30", "inputs": "2"}, GATE2_30)
wire((1080, 700), (1110, 700)); wire((1110, 700), (1140, 700)); out_pin((1140, 700), "ALARM")
wire((1110, 700), (1110, 740)); led((1110, 740), "ALARM_LED")
# counter state output pins
tunnel((1100, 640), "Q1"); wire((1100, 640), (1140, 640)); out_pin((1140, 640), "CNT1")
tunnel((1100, 660), "Q0"); wire((1100, 660), (1140, 660)); out_pin((1140, 660), "CNT0")

# ================= STEP 6: DISPLAY DECODER =================
text((650, 870), "STEP 6 - BCD TO 7-SEGMENT DECODER (from registered level R1 R0)")
# inverters
tunnel((700, 900), "R1"); wire((700, 900), (730, 900))
comp("1", "NOT Gate", (760, 900), {"size": "30"}, NOT_30)
wire((760, 900), (790, 900)); tunnel((790, 900), "NR1")
tunnel((700, 940), "R0"); wire((700, 940), (730, 940))
comp("1", "NOT Gate", (760, 940), {"size": "30"}, NOT_30)
wire((760, 940), (790, 940)); tunnel((790, 940), "NR0")
# a = d = R1 + /R0
tunnel((840, 900), "R1"); wire((840, 900), (870, 900))
tunnel((840, 920), "NR0"); wire((840, 920), (870, 920))
comp("1", "OR Gate", (900, 910), {"size": "30", "inputs": "2"}, GATE2_30)
wire((900, 910), (930, 910)); tunnel((930, 910), "SA")
# c = /R1 + R0
tunnel((840, 960), "NR1"); wire((840, 960), (870, 960))
tunnel((840, 980), "R0"); wire((840, 980), (870, 980))
comp("1", "OR Gate", (900, 970), {"size": "30", "inputs": "2"}, GATE2_30)
wire((900, 970), (930, 970)); tunnel((930, 970), "SC")
# f = NOR(R1, R0)
tunnel((830, 1020), "R1"); wire((830, 1020), (860, 1020))
tunnel((830, 1040), "R0"); wire((830, 1040), (860, 1040))
comp("1", "NOR Gate", (900, 1030), {"size": "30", "inputs": "2"}, NGATE2_30)
wire((900, 1030), (930, 1030)); tunnel((930, 1030), "SF")
# e = /R0 ; g = R1 ; b = 1
tunnel((700, 1070), "NR0"); wire((700, 1070), (730, 1070)); tunnel((730, 1070), "SE")
tunnel((700, 1100), "R1"); wire((700, 1100), (730, 1100)); tunnel((730, 1100), "SG")
comp("0", "Constant", (710, 1140), {"value": "0x1"})
wire((710, 1140), (730, 1140)); tunnel((730, 1140), "SB")
# 7-segment display: loc (1060,960); north g f a b / south e d c dp
comp("5", "7-Segment Display", (1060, 960), None, SEG7)
tunnel((1060, 920), "SG"); wire((1060, 920), (1060, 960))
tunnel((1070, 900), "SF"); wire((1070, 900), (1070, 960))
tunnel((1080, 880), "SA"); wire((1080, 880), (1080, 960))
tunnel((1090, 860), "SB"); wire((1090, 860), (1090, 960))
tunnel((1060, 1060), "SE"); wire((1060, 1020), (1060, 1060))
tunnel((1070, 1080), "SA"); wire((1070, 1020), (1070, 1080))   # d = a
tunnel((1080, 1100), "SC"); wire((1080, 1020), (1080, 1100))
comp("0", "Constant", (1090, 1060), {"value": "0x0"})
wire((1090, 1020), (1090, 1060))                               # dp off
# segment observation pins
for y, (t, lab) in zip(range(860, 1101, 40),
                       [("SA", "SEG_A"), ("SB", "SEG_B"), ("SC", "SEG_C"),
                        ("SA", "SEG_D"), ("SE", "SEG_E"), ("SF", "SEG_F"), ("SG", "SEG_G")]):
    tunnel((1260, y), t); wire((1260, y), (1300, y)); out_pin((1300, y), lab)

# ================= sanity checks =================
def seg_points(w):
    (x1, y1), (x2, y2) = w
    if x1 == x2:
        return [(x1, y) for y in range(min(y1, y2), max(y1, y2) + 1, 10)]
    return [(x, y1) for x in range(min(x1, x2), max(x1, x2) + 1, 10)]

# 1. every wire endpoint must terminate on a component port or another wire endpoint
endpoints = set()
for w in wires:
    endpoints.update(w)
port_set = set(ports)
for w in wires:
    for p in w:
        touching = p in port_set or sum(1 for v in wires if p in v) > 1
        assert touching, f"dangling wire end {p}"

# 2. no wire endpoint may sit mid-segment of another wire (unintended T junction);
#    branches must share endpoints exactly
for w in wires:
    interior = seg_points(w)[1:-1]
    for p in interior:
        assert p not in endpoints, f"endpoint {p} lands mid-segment of {w}"
        assert p not in port_set, f"port {p} sits mid-segment of {w}"

# 3. overlapping collinear segments of different nets would short: forbid any
#    two wires sharing 2+ consecutive interior points
allpts = {}
for i, w in enumerate(wires):
    pts = seg_points(w)
    for a, b in zip(pts, pts[1:]):
        key = (a, b)
        assert key not in allpts, f"wires {allpts.get(key)} and {i} overlap on {key}"
        allpts[key] = i

# 4. component ports must not collide with each other (two ports same point = direct join; only allowed intentionally - here: never)
seen = {}
for p in ports:
    assert p not in seen, f"two component ports at {p}"
    seen[p] = True

# ================= emit XML =================
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

out = []
out.append('<?xml version="1.0" encoding="UTF-8" standalone="no"?>')
out.append('<project source="2.7.1" version="1.0">')
out.append('This file is intended to be loaded by Logisim (http://www.cburch.com/logisim/).')
out.append('  <lib desc="#Wiring" name="0"/>')
out.append('  <lib desc="#Gates" name="1"/>')
out.append('  <lib desc="#Plexers" name="2"/>')
out.append('  <lib desc="#Arithmetic" name="3"/>')
out.append('  <lib desc="#Memory" name="4"/>')
out.append('  <lib desc="#I/O" name="5"/>')
out.append('  <lib desc="#Base" name="6"/>')
out.append('  <main name="main"/>')
out.append('  <options>')
out.append('    <a name="gateUndefined" val="ignore"/>')
out.append('    <a name="simlimit" val="1000"/>')
out.append('    <a name="simrand" val="0"/>')
out.append('  </options>')
out.append('  <mappings>')
out.append('    <tool lib="6" map="Button2" name="Menu Tool"/>')
out.append('    <tool lib="6" map="Button3" name="Menu Tool"/>')
out.append('    <tool lib="6" map="Ctrl Button1" name="Menu Tool"/>')
out.append('  </mappings>')
out.append('  <toolbar>')
out.append('    <tool lib="6" name="Poke Tool"/>')
out.append('    <tool lib="6" name="Edit Tool"/>')
out.append('    <tool lib="0" name="Pin"/>')
out.append('  </toolbar>')
out.append('  <circuit name="main">')
out.append('    <a name="circuit" val="main"/>')
for lib, name, (x, y), attrs in comps:
    if attrs:
        out.append(f'    <comp lib="{lib}" loc="({x},{y})" name="{name}">')
        for k, v in attrs.items():
            out.append(f'      <a name="{k}" val="{esc(v)}"/>')
        out.append('    </comp>')
    else:
        out.append(f'    <comp lib="{lib}" loc="({x},{y})" name="{name}"/>')
for (x1, y1), (x2, y2) in wires:
    out.append(f'    <wire from="({x1},{y1})" to="({x2},{y2})"/>')
out.append('  </circuit>')
out.append('</project>')

import sys
path = sys.argv[1] if len(sys.argv) > 1 else "water_tank.circ"
with open(path, "w") as f:
    f.write("\n".join(out) + "\n")
print(f"wrote {path}: {len(comps)} components, {len(wires)} wires - all sanity checks passed")
