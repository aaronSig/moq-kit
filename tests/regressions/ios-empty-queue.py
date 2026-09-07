#!/usr/bin/env python3
"""Exercise the actual renderer's empty-input handler with a queued display tail.

AVSampleBufferDisplayLayer consumes compressed input ahead of presentation. An
empty compressed queue must never itself authorize a rendition replacement.
"""
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
source = (lab / 'ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift').read_text()
start = source.index('    private func handleNoActiveFrame()')
end = source.index('\n    private func ', start + 1)
handler = source[start:end]
harness = '''import Foundation
final class Target {
    var stops = 0
    func stopRequestingMediaData() { stops += 1 }
}
final class RendererProbe {
    let renderTarget = Target()
    var hasLoggedNoActiveFrame = false
    var unsafePromotions = 0
    var stallEvaluations = 0
    var enqueueArms = 0
    var pendingHasKeyframe = false
    func promotePendingTrackIfReady() -> Bool {
        if pendingHasKeyframe { unsafePromotions += 1; return true }
        return false
    }
    func armVideoEnqueue() { enqueueArms += 1 }
    func evaluateVideoStallStart() { stallEvaluations += 1 }
    func emptyCompressedQueue() { handleNoActiveFrame() }
HANDLER
}
var failures = 0
for (name, keyframe) in [("cached keyframe with display tail",true), ("fresh keyframe before trial readiness",true), ("no pending keyframe",false)] {
    let renderer = RendererProbe()
    renderer.pendingHasKeyframe = keyframe
    renderer.emptyCompressedQueue()
    let passed = renderer.unsafePromotions == 0 && renderer.stallEvaluations == 1 && renderer.renderTarget.stops == 1
    print("\\(passed ? "PASS" : "FAIL"): \\(name)")
    if !passed { failures += 1 }
}
exit(failures == 0 ? 0 : 1)
'''.replace('HANDLER', handler)
with tempfile.TemporaryDirectory(prefix='moq-ios-empty-queue-') as temporary:
    folder = Path(temporary)
    (folder / 'main.swift').write_text(harness)
    subprocess.run(['swiftc', '-module-cache-path', str(folder / 'modules'), str(folder / 'main.swift'), '-o', str(folder / 'test')], check=True)
    raise SystemExit(subprocess.run([str(folder / 'test')]).returncode)
