# --- SIMPLE FUNC GEN TEST ---

from pyrpl import Pyrpl

HOSTNAME = "192.168.1.98"
p = Pyrpl(hostname=HOSTNAME, config="interface-test-00.yaml")
r = p.rp
s = r.scope

asg = r.asg0

asg.output_direct = "out1"
asg.setup(
    waveform="sin",
    frequency=1e3,
    amplitude=0.5,
    offset=0,
    trigger_source="immediately"
)

print("1 MHz sine going to out1")