#!/usr/bin/env python3
"""Exercise the actual DEBUG injection entry point with controlled owner state.

This validates its guards and bounded call through the real recovery entry point.
It does not simulate an AVFoundation driver or malformed media bitstream.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift").read_text()
start = source.index("    func injectVideoDecoderFailureForTesting(")
brace = source.index("{", start)
depth, end = 1, brace + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]
assert source.rfind("#if DEBUG", 0, start) > source.rfind("#endif", 0, start)
for path in ["Player.swift", "internal/playback/PlaybackPipeline.swift"]:
    other = (root / "ios/Sources/MoQKit/Subscribe" / path).read_text()
    point = other.index("func injectVideoDecoderFailureForTesting(")
    assert other.rfind("#if DEBUG", 0, point) > other.rfind("#endif", 0, point)

harness = r'''import Foundation
enum PipelinePolicies { static let recovery = RecoveryPolicy() }
struct RecoveryPolicy { let maxRecoveries = 2 }
struct Track { var trackName = "hevc-high"; var playbackEpoch: UInt64 = 17; var isPlaybackActive = true }
final class Replay: @unchecked Sendable {
    let enqueueQueue = DispatchQueue(label: "test.video")
    var activeTrack = Track()
    var reportedFatalVideoFailure: (trackName: String, epoch: UInt64)?
    var triggers: [String] = []
    func recoverDisplayIfNeeded(injectedFailure: String? = nil) -> Bool {
        triggers.append(injectedFailure ?? "MISSING LABEL")
        if triggers.count > PipelinePolicies.recovery.maxRecoveries {
            reportedFatalVideoFailure = (activeTrack.trackName, activeTrack.playbackEpoch)
            return false
        }
        return true
    }
METHOD
}
@main struct Checks {
    static func main() async {
        var failures = 0
        func check(_ condition: Bool, _ label: String) {
            print("\(condition ? "PASS" : "FAIL"): \(label)")
            if !condition { failures += 1 }
        }
        let player = Replay()
        let wrongTrack = await player.injectVideoDecoderFailureForTesting(expectedTrackName: "hevc-low", epoch: 17)
        check(!wrongTrack && player.triggers.isEmpty, "pending or stale track cannot inject a failure")
        let wrongEpoch = await player.injectVideoDecoderFailureForTesting(expectedTrackName: "hevc-high", epoch: 16)
        check(!wrongEpoch && player.triggers.isEmpty, "retired playback epoch cannot inject a failure")
        player.activeTrack.isPlaybackActive = false
        let inactive = await player.injectVideoDecoderFailureForTesting(expectedTrackName: "hevc-high", epoch: 17)
        check(!inactive && player.triggers.isEmpty, "inactive decoder cannot inject a failure")
        player.activeTrack.isPlaybackActive = true
        let applied = await player.injectVideoDecoderFailureForTesting(expectedTrackName: "hevc-high", epoch: 17)
        check(applied && player.triggers.count == 3 && player.reportedFatalVideoFailure != nil,
              "active owner exhausts the bounded recovery budget")
        check(player.triggers.allSatisfy { $0 == "Controlled test: active video decoder failure" },
              "every injected recovery is explicitly labelled as controlled")
        let duplicate = await player.injectVideoDecoderFailureForTesting(expectedTrackName: "hevc-high", epoch: 17)
        check(!duplicate && player.triggers.count == 3, "fatal owner cannot be injected twice")
        exit(failures == 0 ? 0 : 1)
    }
}
'''.replace("METHOD", method)
with tempfile.TemporaryDirectory(prefix="moq-video-injection-") as directory:
    folder = Path(directory)
    (folder / "main.swift").write_text(harness)
    subprocess.run(["swiftc", "-parse-as-library", "-module-cache-path", str(folder / "modules"),
                    str(folder / "main.swift"), "-o", str(folder / "test")], check=True)
    raise SystemExit(subprocess.run([str(folder / "test")]).returncode)
