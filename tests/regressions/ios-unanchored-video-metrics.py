#!/usr/bin/env python3
"""Replay actual renderer metric getters with an explicit clock anchor adapter."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift").read_text()
def declaration(marker):
    start = source.index(marker)
    brace = source.index("{", start)
    depth, end = 1, brace + 1
    while depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    return source[start:end]

harness = r'''import Foundation
struct Clock { var isAnchored = false }
struct Track { var latestAdmittedPtsUs: UInt64? = 10_638_410_621 }
final class Renderer {
    var timing = Clock(), activeTrack = Track()
    var playhead: UInt64 = 0
    var reads = 0
    func syncOnEnqueueQueue<T>(_ block: () -> T) -> T { block() }
    func currentSourceVideoTimeUs() -> UInt64 { reads += 1; return playhead }
GETTERS
}
let renderer = Renderer()
var failures = 0
func check(_ value: Bool, _ label: String) {
    print("\(value ? "PASS" : "FAIL"): \(label)")
    if !value { failures += 1 }
}
check(renderer.bufferedAhead == nil, "unanchored media does not report source uptime as buffer reserve")
check(renderer.sourcePlaybackPositionUs == nil && renderer.reads == 0,
      "unknown clock does not update track playback position while measuring")
renderer.timing.isAnchored = true
renderer.playhead = 10_637_710_621
check(renderer.bufferedAhead == .microseconds(700_000), "anchored buffer reports the actual reserve")
renderer.playhead = 0; renderer.activeTrack.latestAdmittedPtsUs = 1_000
check(renderer.sourcePlaybackPositionUs == 0 && renderer.bufferedAhead == .microseconds(1_000),
      "explicitly anchored zero is a valid media timestamp")
exit(failures == 0 ? 0 : 1)
'''.replace("GETTERS", declaration("    var bufferedAhead:") + "\n" + declaration("    var sourcePlaybackPositionUs:"))
with tempfile.TemporaryDirectory(prefix="moq-unanchored-metrics-") as temporary:
    folder = Path(temporary)
    (folder / "main.swift").write_text(harness)
    subprocess.run(["swiftc", "-module-cache-path", str(folder / "modules"), str(folder / "main.swift"),
                    "-o", str(folder / "test")], check=True)
    raise SystemExit(subprocess.run([str(folder / "test")]).returncode)
