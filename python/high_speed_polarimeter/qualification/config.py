"""Experiment-specific settings. Unknown electrical limits default to unconfirmed."""
from dataclasses import dataclass, field, fields
import math
from ..config import DEFAULT_RESULTS


@dataclass(frozen=True)
class QualificationConfig:
    results_directory: str = DEFAULT_RESULTS
    mode: str = 'voltage_transfer'
    strategy: str = 'settled'
    eom_identity: str = ''
    amplifier_identity: str = ''
    wavelength_nm: float = 830.0
    tec_conditions: str = ''
    voltage_chain: str = ''
    optical_arrangement_confirmed: bool = False
    output_map_confirmed: bool = False
    limits_confirmed: bool = False
    startup_zero_profile_confirmed: bool = False
    independent_protection_confirmed: bool = False
    adc_ranges_confirmed: bool = False
    rp_hostname: str = '192.168.1.98'
    rp_config: str = 'scope_config'
    rp_output: str = 'out1'
    pd_input: str = 'in1'
    monitor_input: str = 'in2'
    pd_full_scale_v: float = 1.0
    monitor_full_scale_v: float = 1.0
    command_min_v: float = 0.0
    command_max_v: float = 0.1
    amplifier_input_gain_v_per_command_v: float = 0.0
    amplifier_input_offset_v: float = 0.0
    amplifier_input_min_v: float = 0.0
    amplifier_input_max_v: float = 0.0
    amplifier_input_margin_v: float = 0.0
    chain_gain_v_per_command_v: float = 0.0
    chain_offset_v: float = 0.0
    chain_margin_v: float = 0.0
    electrode_min_v: float = 0.0
    electrode_max_v: float = 0.0
    monitor_available: bool = True
    monitor_calibration_confirmed: bool = False
    monitor_scale_v_per_v: float = 0.0
    monitor_polarity: int = 1
    monitor_zero_v: float = 0.0
    monitor_scale_rel_uncertainty: float = 0.0
    monitor_discrepancy_v: float = 0.0
    start_command_v: float = 0.0
    stop_command_v: float = 0.1
    points: int = 31
    repeats: int = 2
    captures_per_point: int = 1
    settle_s: float = 0.5
    ramp_s: float = 0.2
    triangle_offset_v: float = 0.05
    triangle_amplitude_v: float = 0.05
    triangle_frequency_hz: float = 1.0
    triangle_cycles: int = 2
    bias_v: float = 0.05
    sine_amplitude_v: float = 0.005
    frequencies_hz: list = field(default_factory=lambda: [10.0, 30.0, 100.0])
    reference_frequency_hz: float = 10.0
    allowed_frequency_min_hz: float = 0.0
    allowed_frequency_max_hz: float = 0.0
    operating_slope_confirmed: bool = False
    slope_validation_run: str = ''
    scope_decimation: int = 0  # 0 = choose an actual power-of-two setting per waveform
    scope_timeout_s: float = 15.0
    min_capture_cycles: float = 5.0
    samples_per_cycle: int = 32
    fit_bins: int = 128
    fit_max_cycles: float = 6.0
    fit_max_residual_fraction: float = 0.1
    minimum_snr: float = 5.0

    def envelope(self):
        if self.mode == 'frequency_response':
            return self.bias_v-self.sine_amplitude_v, self.bias_v+self.sine_amplitude_v
        if self.strategy == 'triangle':
            return self.triangle_offset_v-self.triangle_amplitude_v, self.triangle_offset_v+self.triangle_amplitude_v
        return min(self.start_command_v,self.stop_command_v), max(self.start_command_v,self.stop_command_v)

    def validate(self, experiment=None):
        defaults = QualificationConfig()
        for f in fields(self):
            value, default = getattr(self,f.name),getattr(defaults,f.name)
            if isinstance(default,float):
                if type(value) not in (int,float) or not math.isfinite(value):
                    raise ValueError(f'{f.name} must be finite')
            elif type(value) is not type(default):
                raise ValueError(f'Invalid type for {f.name}')
        if self.mode not in {'voltage_transfer','frequency_response'} or self.strategy not in {'settled','triangle'}:
            raise ValueError('Unknown qualification mode/strategy')
        if self.rp_output not in {'out1','out2'} or {self.pd_input,self.monitor_input}!={'in1','in2'}:
            raise ValueError('Select one RP output and distinct physical IN1/IN2 PD/monitor inputs')
        if not (-1<=self.command_min_v<=0<=self.command_max_v<=1) or self.command_min_v==self.command_max_v:
            raise ValueError('RP command bounds must lie within ±1 V and include zero cleanup')
        lo,hi=self.envelope()
        if lo < self.command_min_v or hi > self.command_max_v or lo==hi:
            raise ValueError('Entire waveform/setpoint envelope must fit the declared RP bounds')
        for name in ('points','repeats','captures_per_point','triangle_cycles','samples_per_cycle','fit_bins'):
            if getattr(self,name)<1: raise ValueError(f'{name} must be positive')
        if self.points<5 or self.samples_per_cycle<16 or self.fit_bins<16:
            raise ValueError('Need >=5 setpoints, >=16 samples/cycle and >=16 fit bins')
        for name in ('settle_s','ramp_s','chain_margin_v','amplifier_input_margin_v','monitor_scale_rel_uncertainty','monitor_discrepancy_v','monitor_scale_v_per_v'):
            if getattr(self,name)<0: raise ValueError(f'{name} must be nonnegative')
        for name in ('wavelength_nm','scope_timeout_s','triangle_frequency_hz','triangle_amplitude_v','sine_amplitude_v','min_capture_cycles','minimum_snr','fit_max_cycles','fit_max_residual_fraction'):
            if getattr(self,name)<=0: raise ValueError(f'{name} must be positive')
        if self.pd_full_scale_v not in (1,20) or self.monitor_full_scale_v not in (1,20):
            raise ValueError('Confirm physical ADC jumper ranges: ±1 V or ±20 V')
        if self.scope_decimation not in {0,*[2**i for i in range(17)]} or self.monitor_polarity not in (-1,1):
            raise ValueError('Invalid decimation or monitor polarity')
        if not self.results_directory.strip() or not self.rp_hostname.strip() or not self.rp_config.strip():
            raise ValueError('Results path and RP profile/address must be nonempty')
        if not self.frequencies_hz or any(type(f) not in (float,int) or not math.isfinite(f) or f<=0 for f in self.frequencies_hz):
            raise ValueError('Frequencies must be finite positive numbers')
        if len(set(self.frequencies_hz))!=len(self.frequencies_hz) or self.reference_frequency_hz not in self.frequencies_hz:
            raise ValueError('Frequencies must be unique and include the explicit reference')
        if self.monitor_calibration_confirmed and (not self.monitor_available or self.monitor_scale_v_per_v<=0):
            raise ValueError('Confirmed monitor calibration requires an available monitor and positive scale')
        if self.mode=='voltage_transfer' and self.strategy=='triangle' and not self.monitor_available:
            raise ValueError('Continuous triangle requires a measured monitor to identify direction/cycles')
        if self.operating_slope_confirmed and not self.slope_validation_run.strip():
            raise ValueError('Record the transfer-curve evidence for a confirmed operating slope')
        self.decimation(None if self.mode=='voltage_transfer' and self.strategy=='settled' else
                        (self.triangle_frequency_hz if self.mode=='voltage_transfer' else min(self.frequencies_hz)))
        if self.mode=='frequency_response':
            for f in self.frequencies_hz: self.decimation(f)
        return self

    def decimation(self, frequency):
        candidates=[self.scope_decimation] if self.scope_decimation else [2**i for i in range(17)]
        for dec in candidates:
            dt=8e-9*dec; duration=16384*dt
            if duration>=self.scope_timeout_s: continue
            needed=max(self.min_capture_cycles,self.triangle_cycles+1) if self.mode=='voltage_transfer' and self.strategy=='triangle' else self.min_capture_cycles
            if frequency is None or (duration*frequency>=needed and dt*frequency<=1/self.samples_per_cycle):
                return dec
        raise ValueError('Requested frequency/cycles/resolution cannot fit one 16384-sample buffer and timeout')

    def confirm_hardware(self):
        self.validate()
        flags=('optical_arrangement_confirmed','output_map_confirmed','limits_confirmed',
               'startup_zero_profile_confirmed','independent_protection_confirmed','adc_ranges_confirmed')
        if not all(getattr(self,k) for k in flags):
            raise ValueError('Hardware requires explicit optical, mapping, electrical-limit, zero-startup-profile, independent-protection and ADC-range confirmations')
        if not all(s.strip() for s in (self.eom_identity,self.amplifier_identity,self.voltage_chain)) or self.chain_gain_v_per_command_v==0:
            raise ValueError('Record EOM/amplifier identities and the complete confirmed chain gain/offset')
        lo,hi=self.envelope()
        amplifier_input=[self.amplifier_input_gain_v_per_command_v*v+self.amplifier_input_offset_v for v in (lo,hi,0)]
        if (self.amplifier_input_gain_v_per_command_v==0 or self.amplifier_input_min_v>=self.amplifier_input_max_v
            or min(amplifier_input)-self.amplifier_input_margin_v<self.amplifier_input_min_v
            or max(amplifier_input)+self.amplifier_input_margin_v>self.amplifier_input_max_v):
            raise ValueError('Predicted preamp-output/HV-amplifier-input envelope exceeds confirmed limits or is unknown')
        # Include zero startup/cleanup and full gain/offset uncertainty envelope.
        predicted=[self.chain_gain_v_per_command_v*v+self.chain_offset_v for v in (lo,hi,0)]
        if not self.electrode_min_v<self.electrode_max_v or min(predicted)-self.chain_margin_v<self.electrode_min_v or max(predicted)+self.chain_margin_v>self.electrode_max_v:
            raise ValueError('Predicted electrode envelope including zero, offsets and margin exceeds confirmed limits')
        fs=self.frequencies_hz if self.mode=='frequency_response' else ([self.triangle_frequency_hz] if self.strategy=='triangle' else [])
        if fs and not (0<self.allowed_frequency_min_hz<=min(fs)<=max(fs)<=self.allowed_frequency_max_hz):
            raise ValueError('Waveforms require operator-confirmed physical frequency limits')
        if self.monitor_calibration_confirmed and self.monitor_discrepancy_v<=0:
            raise ValueError('Set a positive allowed monitor/drive discrepancy in electrode volts')
        return self
