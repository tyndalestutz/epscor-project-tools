"""Offline evidence for the rough PAX-only retardance routine."""
from .analysis.vpi import analyze
from .io import read_json
from .quality import QualityCheck, QualityStatus, RunQuality


def evaluate(directory):
    result = analyze(directory)
    run = read_json(directory/'run.json')
    evidence = {'artifact': 'analysis.json', 'source_sha256': result['source_sha256']}
    checks = [QualityCheck('execution_and_cleanup', QualityStatus.PASS if run['status']=='COMPLETED' and run['cleanup_status']=='COMPLETED' else QualityStatus.FAIL,
                           'Check retained run status and zero/cleanup evidence; no physical voltage measurement.', evidence={'artifact':'run.json'})]
    if result['status'] == 'SYNTHETIC_ONLY':
        checks.append(QualityCheck('optical_vpi',QualityStatus.UNKNOWN,'Synthetic software check; no optical Vpi inference.',evidence=evidence))
    else:
        baseline = result['undriven_baseline']
        requested = read_json(directory/'recipe.json')['config']['undriven_baseline_s']
        covered = baseline.get('covered_s',0)
        checks.append(QualityCheck('undriven_baseline_coverage',QualityStatus.PASS if covered>=.9*requested else QualityStatus.WARN,
            'Time average uses only retained valid adjacent intervals; omitted gaps remain explicit.',measured_value=covered,units='s',
            thresholds={'minimum_fraction_of_requested':.9,'requested_s':requested},evidence=evidence))
        checks.append(QualityCheck('dop_direction_uncertainty', QualityStatus.WARN if result['dop_warning_count'] else QualityStatus.PASS,
            f"{result['dop_warning_count']} low/invalid DoP records; all retained without filtering.",
            thresholds={'warn_below_dop':read_json(directory/'recipe.json')['config']['vpi_warn_below_dop']},evidence=evidence))
        checks.append(QualityCheck('raw_dop_above_one',QualityStatus.WARN if result['dop_above_one_count'] else QualityStatus.PASS,
            f"{result['dop_above_one_count']} raw DoP readings above 1; retained without clipping. Values above 1 are a measurement/calibration limitation, not physical DoP above 100%.",evidence=evidence))
        for cycle in result['cycles']:
            supported = cycle['status']=='MODEL_SUPPORTED'
            reached = supported and all(fit['pi_reached'] for fit in cycle['fits'])
            checks.append(QualityCheck(f'cycle_{cycle["cycle"]}_model', QualityStatus.PASS if supported else QualityStatus.WARN,
                'Fixed-axis and linear-branch checks supported.' if supported else cycle.get('reason','Linear branch unsupported; inspect residuals.'), evidence=evidence))
            checks.append(QualityCheck(f'cycle_{cycle["cycle"]}_pi_coverage', QualityStatus.PASS if reached else QualityStatus.WARN,
                'Pi change observed; crossing is interpolated between adjacent sampled commands.' if reached else 'Pi not established: any linear-branch Vpi is extrapolated, not an achieved operating point.', evidence=evidence))
        checks.append(QualityCheck('calibration_validity',QualityStatus.UNKNOWN,
            'Rough model-based check; hysteresis is confounded with settling/time drift. Delivered voltage and temperature unmeasured.',evidence=evidence))
    return RunQuality(tuple(checks),'eom_vpi_hysteresis','1')
