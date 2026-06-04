from pyrpl import Pyrpl
import numpy as np
import time

HOST = "192.168.1.98"
CONFIG = "interface-test-00.yaml"

p = Pyrpl(hostname=HOST, config=CONFIG)
r = p.rp

# -------------------------
# CLEAN STATE
# -------------------------
r.asg0.output_direct = "off"
r.pid0.output_direct = "off"
r.iq0.output_direct = "off"
