#!/usr/bin/env python3
"""Replay the real renderer recovery method with an AVFoundation failure adapter.

This exercises flush exhaustion and event ownership. It does not claim to induce
an AVFoundation driver failure or prove a physical codec fallback.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / "ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift").read_text()
start = source.index("    private func recoverDisplayIfNeeded(")
brace = source.index("{", start)
depth, end = 1, brace + 1
while depth:
    depth += (source[end] == "{") - (source[end] == "}")
    end += 1
method = source[start:end]
controller = (root / "ios/Sources/MoQKit/Subscribe/internal/pipeline/VideoRecoveryController.swift").read_text()

harness = r'''import Foundation
typealias TrackEpoch = UInt64
enum RecoveryStep { case flush, fail }
struct RecoveryPolicy { let windowNanos: UInt64; let maxRecoveries: Int }
enum PipelinePolicies { static let recovery = RecoveryPolicy(windowNanos: 1_000_000_000, maxRecoveries: 2) }
protocol PipelineTimeSource { var nowNanos: UInt64 { get } }
struct MonotonicPipelineTimeSource: PipelineTimeSource { let nowNanos: UInt64 = 100 }
enum RenderStatus { case rendering, failed }
final class RenderTarget {
    var status = RenderStatus.rendering
    var requiresFlushToResumeDecoding = false
    var error: NSError? = NSError(domain: "test", code: 1, userInfo: [NSLocalizedDescriptionKey: "bad HEVC frame"])
    var flushes = 0
    func flush(removeDisplayedImage: Bool) { flushes += 1 }
}
struct VideoRendererSample {}
enum TimelineDecision<T> { case reset(Int, UInt64, Int, Int) }
struct Timeline { func requestReset<T>() -> TimelineDecision<T> { .reset(0, 1, 0, 0) } }
struct Depth { let frames = 2 }
final class Track {
    var trackName = "hevc-high"
    var playbackEpoch: UInt64 = 17
    let timeline = Timeline(), diagnosticDepth = Depth()
    func flush() {}
}
struct Horizon { mutating func resetCoverage() {} }
struct PipelineError { let code, message: String }
enum ResetReason { case localReset }
enum FlushReason { case decoderRecovery }
enum Event {
    case decoderRecovery(context: Int, attempt: Int, step: RecoveryStep, trigger: String)
    case transportClosed(context: Int, error: PipelineError?)
    case discontinuity(context: Int, epoch: UInt64, reason: ResetReason)
}
final class Bus {
    var closed = 0
    func emit(_ event: Event) { if case .transportClosed = event { closed += 1 } }
}
final class Delegate {
    var failures: [(String, UInt64)] = []
    func videoRenderer(_ renderer: Replay, didFailDecodingTrack name: String, trackEpoch: UInt64, message: String) {
        failures.append((name, trackEpoch))
    }
}
final class Replay {
    let renderTarget = RenderTarget(), recoveryController = VideoRecoveryController(), pipelineBus = Bus()
    let activeTrack = Track()
    var delegate: Delegate? = Delegate()
    var reportedFatalVideoFailure: (trackName: String, epoch: TrackEpoch)?
    var stallHorizon = Horizon()
    func pipelineContext(for track: Track) -> Int { 1 }
    func cancelVideoStallCheck() {}
    func emitDisplayFlush(reason: FlushReason, trigger: String, droppedFrames: Int, track: Track) {}
    func recover() -> Bool { recoverDisplayIfNeeded() }
METHOD
}
var failures = 0
func check(_ value: Bool, _ name: String) {
    print("\(value ? "PASS" : "FAIL"): \(name)")
    if !value { failures += 1 }
}
let player = Replay()
check(player.recover() && player.renderTarget.flushes == 0, "healthy output does not start recovery")
player.renderTarget.status = .failed
check(player.recover() && player.recover(), "bounded flush attempts remain available")
check(player.delegate!.failures.isEmpty, "recoverable output errors do not request a codec change")
check(!player.recover(), "exhausted decoder budget stops output")
check(player.delegate!.failures.count == 1 && player.delegate!.failures[0].0 == "hevc-high"
      && player.delegate!.failures[0].1 == 17, "fatal event identifies the actual active track and epoch")
check(!player.recover() && player.delegate!.failures.count == 1 && player.pipelineBus.closed == 1,
      "repeated callbacks cannot report the same fatal failure twice")
player.activeTrack.playbackEpoch = 18
check(!player.recover() && player.delegate!.failures.count == 2
      && player.delegate!.failures[1].1 == 18, "a new playback epoch owns its own failure")
player.activeTrack.trackName = "hevc-low"
check(!player.recover() && player.delegate!.failures.count == 3,
      "a different active track is not suppressed by the previous owner")
exit(failures == 0 ? 0 : 1)
'''.replace("METHOD", method)
with tempfile.TemporaryDirectory(prefix="moq-fatal-video-") as folder:
    folder = Path(folder)
    (folder / "main.swift").write_text(controller + "\n" + harness)
    subprocess.run(["swiftc", "-module-cache-path", str(folder / "modules"), str(folder / "main.swift"),
                    "-o", str(folder / "test")], check=True)
    raise SystemExit(subprocess.run([str(folder / "test")]).returncode)
