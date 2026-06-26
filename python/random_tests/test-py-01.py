# --- ROUTING IN1 -> OUT1 ---

from pyrpl import Pyrpl

HOSTNAME = "192.168.1.98"
p = Pyrpl(hostname=HOSTNAME, config="interface-test-00.yaml")
r = p.rp

# --- HARD RESET OF OUTPUT ROUTING ---
r.asg0.output_direct = "off"
r.asg1.output_direct = "off"

r.pid0.output_direct = "off"
r.pid1.output_direct = "off"
r.pid2.output_direct = "off"

r.iq0.output_direct = "off"
r.iq1.output_direct = "off"
r.iq2.output_direct = "off"

pid = r.pid0

pid.input = "in1"

pid.p = 1.0
pid.i = 0.0
pid.ival = 0.0

pid.inputfilter = []

pid.output_direct = "out1"

print("PID pass-through enabled")