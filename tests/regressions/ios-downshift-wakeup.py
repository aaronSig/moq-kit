#!/usr/bin/env python3
"""Exercise actual switch timer registration and callback with a virtual queue.

The stalled renderer has no active input and replacement deltas do not issue
keyframe notifications. A pending downshift must still recheck readiness.
"""
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
source = (lab / 'ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift').read_text()
name = 'schedulePendingSwitchCheck' if 'schedulePendingSwitchCheck' in source else 'scheduleUpgradeGuard'
start = source.index('    private func '+name+'(')
end = source.index('\n    private func ', start+1)
registration = source[source.index('            self.scheduleSwitchTimeout()'):source.index('            self.armVideoEnqueue()', source.index('            self.scheduleSwitchTimeout()'))]
harness = '''import Foundation
final class VideoRendererTrack {}
final class Queue {
    var callbacks: [() -> Void] = []
    func asyncAfter(deadline: DispatchTime, execute: @escaping () -> Void) { callbacks.append(execute) }
    func runOne() { if !callbacks.isEmpty { callbacks.removeFirst()() } }
}
final class RendererProbe {
    let enqueueQueue = Queue()
    var activeTrack = VideoRendererTrack()
    var pendingTrack: VideoRendererTrack? = VideoRendererTrack()
    var ready = false
    var arms = 0
    var checks = 0
    func scheduleSwitchTimeout() {}
    func advancePendingTrackSwapIfNeeded() {
        checks += 1
        if ready, let pending = pendingTrack { activeTrack = pending; pendingTrack = nil }
    }
    func armVideoEnqueue() { arms += 1 }
    func start(minimumTrialLeadUs: UInt64) {
        let track = pendingTrack!
REGISTRATION
    }
HANDLER
}
var failures = 0
func check(_ name: String, _ passed: Bool) {
    print("\\(passed ? "PASS" : "FAIL"): \\(name)")
    if !passed { failures += 1 }
}
for lead: UInt64 in [0, 595000] {
    let p = RendererProbe()
    p.start(minimumTrialLeadUs:lead)
    // Replacement becomes usable without another keyframe callback.
    p.ready = true
    p.enqueueQueue.runOne()
    check("ready switch with lead=\\(lead) wakes renderer", p.arms == 1 && p.pendingTrack == nil)
}
let waiting = RendererProbe()
waiting.start(minimumTrialLeadUs:0)
waiting.enqueueQueue.runOne()
check("unready downshift stays pending and rechecks", waiting.checks == 1 && waiting.arms == 0 && waiting.enqueueQueue.callbacks.count == 1)
let stale = RendererProbe()
stale.start(minimumTrialLeadUs:0)
let obsolete = stale.pendingTrack!
stale.pendingTrack = VideoRendererTrack()
stale.ready = true
withExtendedLifetime(obsolete) { stale.enqueueQueue.runOne() }
check("obsolete callback cannot promote another switch", stale.checks == 0 && stale.arms == 0 && stale.enqueueQueue.callbacks.isEmpty)
exit(failures == 0 ? 0 : 1)
'''.replace('REGISTRATION',registration).replace('HANDLER',source[start:end])
with tempfile.TemporaryDirectory(prefix='moq-ios-downshift-wakeup-') as tmp:
    folder = Path(tmp)
    (folder/'main.swift').write_text(harness)
    subprocess.run(['swiftc','-module-cache-path',str(folder/'modules'),str(folder/'main.swift'),'-o',str(folder/'test')],check=True)
    raise SystemExit(subprocess.run([str(folder/'test')]).returncode)
