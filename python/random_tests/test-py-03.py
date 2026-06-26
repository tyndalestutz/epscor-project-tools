# --- STEP 2: SINGLE-FREQUENCY SYSTEM IDENTIFICATION VIA SPECTRUM ANALYZER ---

from pyrpl import Pyrpl
import numpy as np
import time

HOSTNAME = "192.168.1.98"
p = Pyrpl(hostname=HOSTNAME, config="interface-test-00.yaml")
r = p.rp

# --------------------------------------------------
# HARD RESET (avoid routing conflicts)
# --------------------------------------------------
r.asg0.output_direct = "off"
r.asg1.output_direct = "off"

r.pid0.output_direct = "off"
r.pid1.output_direct = "off"
r.pid2.output_direct = "off"

r.iq0.output_direct = "off"
r.iq1.output_direct = "off"
r.iq2.output_direct = "off"

# --------------------------------------------------
# STEP 2A: DRIVE EOM (known sinusoid)
# --------------------------------------------------
asg = r.asg0
drive_freq = 1010        # 1.01 kHz test tone
drive_amp = 0.1

asg.output_direct = "out1"
asg.setup(
    waveform="sin",
    frequency=drive_freq,
    amplitude=drive_amp,
    offset=0,
    trigger_source="immediately"
)

print(f"Driving EOM at {drive_freq} Hz")

# --------------------------------------------------
# STEP 2B: SETUP SPECTRUM ANALYZER
# --------------------------------------------------
na = p.spectrumanalyzer

na.setup(
    input='in1',
    baseband=True,
    span=50e3,
    window='blackman'
)

time.sleep(0.5)  # let system settle

# --------------------------------------------------
# STEP 2C: TAKE SPECTRUM
# --------------------------------------------------
ch1, ch2, cross_re, cross_im = na.single()

freqs = na.frequencies

# --------------------------------------------------
# STEP 2D: EXTRACT RESPONSE AT DRIVE FREQUENCY
# --------------------------------------------------
idx = np.argmin(np.abs(freqs - drive_freq))

signal_peak = ch1[idx]

print("\n--- STEP 2 RESULT ---")
print("Drive frequency:", drive_freq, "Hz")
print("Measured spectral peak:", signal_peak)

# optional sanity print
print("Noise floor estimate:", np.median(ch1))