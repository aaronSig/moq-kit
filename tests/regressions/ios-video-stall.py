#!/usr/bin/env python3
"""Replay a stalled video clock through the renderer's actual scheduling methods.

The clock, output horizon and event sink are adapters. AVFoundation hardware
presentation is covered by device tests, not this deterministic regression.
"""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'ios/Sources/MoQKit/Subscribe/internal/playback/VideoRenderer.swift').read_text()
def method(name):
    start = source.index('    private func ' + name)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]

harness = '''import Foundation
struct RenderDelay { let frontDisplayTimeUs, playheadUs, renderLeadUs: UInt64 }
final class Clock {
    var isVideoDriven = true
    var time: UInt64 = 1_000_000
    var rate = 0.0
    func setRate(_ rate: Double, timeUs: UInt64? = nil) {
        self.rate = rate
        if let timeUs { time = timeUs }
    }
}
struct Horizon { var isStalled = true; var latestSubmittedPTSUs: UInt64? }
enum Event { case stallEnded(context: Int, cause: Int, durationMillis: UInt64) }
final class Bus { func emit(_ event: Event) {} }
final class Replay {
    let timing = Clock(), pipelineBus = Bus()
    var stallHorizon = Horizon()
    var lastKnownClockTimeUs: UInt64 = 1_000_000
    var videoStallStartedNanos: UInt64? = nil
    var videoStallCause: Int? = nil
    var activeTrack = 0
    func pipelineContext(for track: Int) -> Int { track }
    func currentPlaybackTimeUs() -> UInt64 { timing.time }
    func renderLeadUs() -> UInt64 { 100_000 }
    func displayTimeUs(forVideoTimeUs timestamp: UInt64) -> UInt64 { timestamp }
    func addClamping(_ a: UInt64, _ b: UInt64) -> UInt64 { a + b }
    func submit(_ timestamp: UInt64) -> Bool {
        guard renderDelay(forVideoTimestampUs: timestamp) == nil else { return false }
        stallHorizon.latestSubmittedPTSUs = timestamp
        if stallHorizon.isStalled { stallHorizon.isStalled = false; endVideoStall() }
        return true
    }
METHODS
}
var failures = 0
func check(_ value: Bool, _ name: String) {
    print("\\(value ? "PASS" : "FAIL"): \\(name)")
    if !value { failures += 1 }
}
let stalled = Replay()
check(stalled.submit(5_000_000), "replacement frame admitted after live source advances")
check(stalled.timing.time == 5_000_000 && stalled.timing.rate == 1, "video clock resumes at replacement media")
check(stalled.submit(5_040_000), "following frame remains inside render window")
let healthy = Replay(); healthy.stallHorizon.isStalled = false; healthy.timing.rate = 1
check(!healthy.submit(5_000_000), "healthy clock still paces future media")
let audio = Replay(); audio.timing.isVideoDriven = false
check(audio.submit(5_000_000) && audio.timing.time == 1_000_000 && audio.timing.rate == 0, "video recovery does not move the audio clock")
exit(failures == 0 ? 0 : 1)
'''.replace('METHODS', method('renderDelay(') + '\n' + method('endVideoStall('))
with tempfile.TemporaryDirectory(prefix='moq-video-stall-') as folder:
    p = Path(folder)
    (p / 'main.swift').write_text(harness)
    subprocess.run(['swiftc', '-module-cache-path', str(p / 'modules'), str(p / 'main.swift'), '-o', str(p / 'test')], check=True)
    raise SystemExit(subprocess.run([str(p / 'test')]).returncode)
