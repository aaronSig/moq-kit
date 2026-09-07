#!/usr/bin/env python3
"""Exercise actual SDK ingest ownership using observable task/subscription doubles."""
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
source = (lab / 'ios/Sources/MoQKit/Subscribe/internal/playback/PlaybackPipelineSupport.swift').read_text()
ownership = source.split('\nextension PlaybackPipeline {', 1)[0]
harness = '''import Foundation
final class MediaTrack: @unchecked Sendable {
    var closeCount = 0
    func close() { closeCount += 1 }
}
OWNERSHIP
var failures = 0
func check(_ ok: Bool, _ label: String) { print("\\(ok ? "PASS" : "FAIL"): \\(label)"); if !ok { failures += 1 } }
func task() -> Task<Void,Never> { Task { try? await Task.sleep(nanoseconds: 30_000_000_000) } }
let lowSub = MediaTrack(), lowTask = task(), activity = VideoIngestActivity()
let retained = TrackIngestHandle(task: lowTask, subscription: lowSub, activity: activity)
retained.close(unless: retained)
check(retained.isRunning && lowSub.closeCount == 0 && !lowTask.isCancelled,"upgrade preserves retained low subscription")
let probeSub = MediaTrack(), probeTask = task()
let probe = TrackIngestHandle(task: probeTask, subscription: probeSub)
probe.close(unless: retained)
check(probeSub.closeCount == 1 && probeTask.isCancelled && retained.isRunning,"failed probe closes only probe demand")
let reused = retained.snapshot()
check(reused.subscription === lowSub && reused.task != nil && retained.isRunning,"downshift reuses without transferring retained ownership")
activity.finish()
check(!retained.isRunning,"naturally finished receive task is not reused")
retained.close(); retained.close(); probe.close()
check(lowSub.closeCount == 1 && lowTask.isCancelled && probeSub.closeCount == 1,"stop closes each resource once")
let oldSub = MediaTrack(), oldTask = task()
let old = TrackIngestHandle(task: oldTask, subscription: oldSub)
let restored = old.take()
old.close()
check(restored.subscription === oldSub && oldSub.closeCount == 0 && !oldTask.isCancelled && !old.isRunning,"abort transfers ordinary active resources without closing them")
restored.task?.cancel(); restored.subscription?.close()
check(oldSub.closeCount == 1 && oldTask.isCancelled,"restored resources retain one closing owner")
exit(failures == 0 ? 0 : 1)
'''.replace('OWNERSHIP', ownership)
with tempfile.TemporaryDirectory(prefix='moq-ios-retained-ingest-') as temporary:
    folder = Path(temporary)
    (folder / 'main.swift').write_text(harness)
    subprocess.run(['swiftc', '-module-cache-path', str(folder / 'modules'), str(folder / 'main.swift'), '-o', str(folder / 'test')], check=True)
    raise SystemExit(subprocess.run([str(folder / 'test')]).returncode)
