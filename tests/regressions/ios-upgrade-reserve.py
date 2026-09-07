#!/usr/bin/env python3
"""Exercise the renderer's actual upgrade-abort branch with deterministic time.

The fixture supplies a healthy 250 ms arrival cadence and a genuinely draining
reserve. Only the wall clock is substituted; guard logic and its controller are
compiled from the pinned SDK checkout.
"""
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
source = lab / 'ios/Sources/MoQKit/Subscribe/internal'
renderer = (source / 'playback/VideoRenderer.swift').read_text()
start = renderer.index('        if minimumTrialLeadUs > 0 {', renderer.index('    private func advancePendingTrackSwapIfNeeded()'))
start = renderer.index('\n', start) + 1
end = renderer.index('            // Preparation reserve is a readiness test', start)
branch = renderer[start:end].replace('DispatchTime.now().uptimeNanoseconds', 'nowNanos')
harness = '''import Foundation
final class Track { var latestAdmittedPtsUs: UInt64? }
final class Probe {
    let activeTrack = Track()
    let switchController = RenditionSwitchController()
    let sourcePlayheadUs: UInt64 = 1_000_000
    var minimumActiveLeadUs: UInt64 = 525_000
    var nowNanos: UInt64 = 0
    var aborted = false
    var abortAt: UInt64?
    init() { switchController.begin(targetTrack: "540p", nowNanos: 0) }
    func abortPendingSwitch(expectedTrialAbort: Bool) {
        precondition(expectedTrialAbort)
        aborted = true; abortAt = nowNanos
        switchController.complete()
    }
    func tick(_ lead: UInt64, at ms: UInt64) {
        guard !aborted else { return }
        nowNanos = ms * 1_000_000
        activeTrack.latestAdmittedPtsUs = sourcePlayheadUs + lead
BRANCH
    }
}
var failures = 0
func check(_ value: Bool, _ label: String) {
    print("\\(value ? "PASS" : "FAIL"): \\(label)")
    if !value { failures += 1 }
}
let healthy = Probe()
for group in 0..<10 {
    for (index, lead) in [623_000,573_000,523_000,473_000,423_000].enumerated() {
        healthy.tick(UInt64(lead), at: UInt64(group*250+index*50))
    }
}
check(!healthy.aborted, "700ms buffer survives normal quarter-second arrivals through upgrade preparation")
let draining = Probe()
for (index, lead) in [620_000,570_000,520_000,470_000,420_000,370_000,320_000].enumerated() {
    draining.tick(UInt64(lead), at: UInt64(index*50))
}
check(draining.aborted && draining.abortAt! <= 300_000_000,
      "sustained starvation aborts while at least320ms of old presentation remains")
let urgent = Probe(); urgent.tick(620_000,at:0); urgent.tick(190_000,at:50)
check(urgent.aborted && urgent.abortAt == 50_000_000,"critical reserve aborts immediately without waiting for debounce")
let recovered = Probe(); recovered.tick(600_000,at:0); recovered.tick(500_000,at:50); recovered.tick(600_000,at:100)
recovered.tick(500_000,at:250); recovered.tick(450_000,at:350)
check(!recovered.aborted,"replenishment clears the preceding pressure interval")
let fresh = Probe(); fresh.tick(500_000,at:0)
fresh.switchController.complete(); fresh.switchController.begin(targetTrack:"720p",nowNanos:300_000_000)
fresh.tick(500_000,at:350)
check(!fresh.aborted,"new trial does not inherit the previous trial's pressure")
let small = Probe(); small.minimumActiveLeadUs = 150_000; small.tick(160_000,at:0)
check(!small.aborted,"hard floor does not exceed the configured reserve for a smaller buffer")
small.tick(140_000,at:50)
check(small.aborted,"a small reserve still fails immediately below its existing safety boundary")
exit(failures == 0 ? 0 : 1)
'''.replace('BRANCH', branch)
with tempfile.TemporaryDirectory(prefix='moq-ios-upgrade-reserve-') as temporary:
    folder = Path(temporary)
    (folder / 'main.swift').write_text(harness)
    subprocess.run(['swiftc', '-module-cache-path', str(folder/'modules'),
                    str(source/'pipeline/PipelinePolicies.swift'),
                    str(source/'pipeline/RenditionSwitchController.swift'),
                    str(folder/'main.swift'), '-o', str(folder/'test')], check=True)
    raise SystemExit(subprocess.run([str(folder/'test')]).returncode)
