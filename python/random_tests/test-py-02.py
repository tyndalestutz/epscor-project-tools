# --- EOM INJECTION TEST ---

from pyrpl import Pyrpl

HOSTNAME = "192.168.1.98"
p = Pyrpl(hostname=HOSTNAME, config="interface-test-00.yaml")
r = p.rp

# Reset outputs
r.asg0.output_direct = "off"
r.pid0.output_direct = "off"

# Drive EOM (connect OUT1 → EOM driver input)
asg = r.asg0
asg.output_direct = "out1"
asg.setup(
    waveform="sin",
    frequency=1e2,   # start LOW (10 Hz)
    amplitude=0.1,
    trigger_source="immediately"
)

print("Injecting 10 Hz into EOM")