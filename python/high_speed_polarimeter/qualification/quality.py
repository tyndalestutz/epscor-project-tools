"""Conservative run evidence; software completion is not a hardware calibration."""
from ..quality import QualityCheck,QualityStatus,RunQuality
from .analyze import analyze


def evaluate(directory):
    result=analyze(directory)
    completed=result['execution_status']=='COMPLETED' and result['cleanup_status']=='COMPLETED'
    evidence={'artifact':'analysis.json','source_hashes':result['source_hashes']}
    checks=[QualityCheck('execution_cleanup',QualityStatus.PASS if completed else QualityStatus.FAIL,
        'Acquisition completion and terminal cleanup status; not electrical safety certification.',evidence=evidence)]
    for flag in result['flags']:
        checks.append(QualityCheck(flag,QualityStatus.UNKNOWN if flag=='SYNTHETIC_NOT_HARDWARE_VERIFIED' else QualityStatus.WARN,
                                  flag.replace('_',' ').lower(),evidence=evidence))
    checks.append(QualityCheck('independent_physical_validation',QualityStatus.UNKNOWN,
        'No intrinsic crystal calibration or bandwidth claim. Independent voltage/analog timing/optical validation required.',evidence=evidence))
    return RunQuality(tuple(checks),'eom_pd_qualification','2')
