#!/usr/bin/env python3
"""Run the actual timed upgrade callback with deterministic queue/track doubles."""
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
source = (lab / 'ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift').read_text()
start = source.index('    private func schedulePendingSwitchCheck(')
end = source.index('\n    private func ', start + 1)
harness = '''import Foundation
final class VideoRendererTrack {}
final class Queue {
    var callbacks: [() -> Void] = []
    func asyncAfter(deadline: DispatchTime, execute: @escaping () -> Void) { callbacks.append(execute) }
    func runOne() { callbacks.removeFirst()() }
}
final class RendererProbe {
    let enqueueQueue = Queue()
    var activeTrack = VideoRendererTrack()
    var pendingTrack: VideoRendererTrack? = VideoRendererTrack()
    var ready = false
    var arms = 0
    func advancePendingTrackSwapIfNeeded() {
        if ready, let pending = pendingTrack { activeTrack = pending; pendingTrack = nil }
    }
    func armVideoEnqueue() { arms += 1 }
    func start() { schedulePendingSwitchCheck(pendingTrack!) }
HANDLER
}
var failures = 0
for ready in [false, true] {
    let renderer = RendererProbe()
    renderer.ready = ready
    renderer.start()
    renderer.enqueueQueue.runOne()
    let passed = ready ? renderer.arms == 1 : (renderer.arms == 0 && renderer.enqueueQueue.callbacks.count == 1)
    print("\\(passed ? "PASS" : "FAIL"): timed guard ready=\\(ready), renderer wakes=\\(renderer.arms)")
    if !passed { failures += 1 }
}
exit(failures == 0 ? 0 : 1)
'''.replace('HANDLER', source[start:end])
with tempfile.TemporaryDirectory(prefix='moq-ios-guard-wakeup-') as temporary:
    folder = Path(temporary)
    (folder / 'main.swift').write_text(harness)
    subprocess.run(['swiftc', '-module-cache-path', str(folder / 'modules'), str(folder / 'main.swift'), '-o', str(folder / 'test')], check=True)
    raise SystemExit(subprocess.run([str(folder / 'test')]).returncode)
