"""Extended slow-consumer observation; original v3 records remain unchanged."""
from timed_regression import (TIMING_POLICY, timing_check, evaluate as evaluate_v3,
    main as main_v3, window as original_window)

SLOW_SECONDS = 128
PROTOCOL = 'g1-continuous-cpu-v4.1-timed-128'

def window(case):
    return SLOW_SECONDS if case['workload']=='slow' else original_window(case)

def evaluate(rows, selected, policy, integrity, plan):
    return evaluate_v3(rows, selected, policy, integrity, plan,
        window_for=window, protocol=PROTOCOL)

def main():
    return main_v3(window_for=window, evaluate_plan=evaluate, protocol=PROTOCOL, description=__doc__,
        observation_basis='Longer slow window selected by a separate complete same-version control-only calibration before candidate measurements; all original performance and timing ceilings unchanged')

if __name__=='__main__':raise SystemExit(main())
