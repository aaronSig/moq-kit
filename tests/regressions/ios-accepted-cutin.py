#!/usr/bin/env python3
"""Replay handoff/pruning against the actual renderer, controller and frame buffer.

Only AVFoundation output, compressed payload construction and time are doubled.
The renderer's whole switch method and the track's queue methods are extracted
verbatim; the real bounded queue and switch controller compile in full.
"""
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
sdk = lab / 'ios/Sources/MoQKit'
root = sdk / 'Subscribe/internal'
renderer = (root/'playback/VideoRenderer.swift').read_text()
track = (root/'playback/VideoRendererTrack.swift').read_text()


def member(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening+1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


track_members = []
for signature in ['func setPlaybackActive(', 'func setBufferState(',
                  'var firstKeyframePts:', 'func discardBeforeNewestKeyframe(',
                  'func discardNonKeyframesBeforePts(', 'func flush(',
                  'func retainCutInKeyframe(', 'func releaseCutInKeyframe(',
                  'func hasCutInCoverage(']:
    if signature in track:
        track_members.append(member(track, signature))
renderer_members = [member(renderer, s).replace('DispatchTime.now().uptimeNanoseconds', 'nowNanos')
                    for s in ['private func advancePendingTrackSwapIfNeeded(',
                              'private func discardStalePendingFrames(',
                              'private func abortPendingSwitch(']]
harness = '''import Foundation
final class Lock { func withLock<T>(_ body: () -> T) -> T { body() } }
extension Duration {
    var microsecondsUInt64Clamped: UInt64 { UInt64(max(0, components.seconds*1_000_000 + components.attoseconds/1_000_000_000_000)) }
}
final class VideoRendererTrack {
    enum State { case buffering, pending, playing }
    let lock = Lock()
    let buffer: FrameBuffer<Int>
    var mode: State = .pending
    var playbackActive = false
    let trackEpoch: UInt64 = 0
    var latestAdmitted: UInt64?
    var latestAdmittedPtsUs: UInt64? { latestAdmitted }
    var isRetainedFallback = false
    let targetBuffering = Duration.milliseconds(700)
    var diagnosticDepth: BufferDepth { buffer.depth() }
    init(maxFrames: Int = 1024) { buffer = FrameBuffer(policy: AdmissionPolicy(maxFrames:maxFrames)) }
    func add(_ pts: UInt64, key: Bool = false) {
        _ = buffer.offer(PipelineFrame(payload:0,timestampUs:Int64(pts),keyframe:key,sizeBytes:1))
        latestAdmitted = max(latestAdmitted ?? 0,pts)
    }
    func gop(_ start: UInt64) {
        for i in 0..<6 { add(start+UInt64(i)*41667,key:i == 0) }
    }
    func peekFront() -> (timestampUs: UInt64, isKeyframe: Bool)? {
        buffer.peekFront().map { (UInt64($0.timestampUs),$0.keyframe) }
    }
    func discardFront() { _ = buffer.removeFront() }
    func setOnDataAvailable(_ callback: (() -> Void)?) {}
TRACK_MEMBERS
}
final class Target { func flush(removeDisplayedImage: Bool) {} }
final class Horizon { func resetCoverage() {} }
final class Probe {
    let switchController = RenditionSwitchController()
    var activeTrack = VideoRendererTrack()
    var pendingTrack: VideoRendererTrack?
    var sourcePlayheadUs: UInt64 = 980000
    var nowNanos: UInt64 = 0
    var trialStartedNs: UInt64 = 0
    var minimumTrialLeadUs: UInt64 = 0
    var minimumActiveLeadUs: UInt64 = 525000
    let renderTarget = Target(), stallHorizon = Horizon()
    var pendingSwitchTimeout: DispatchWorkItem?
    var onTrackAborted: ((Bool) -> Void)?
    var onTrackActivated: (() -> Void)?
    var swappedPts: UInt64?
    var phases: [SwitchPhase] = []
    init(_ pending: VideoRendererTrack = VideoRendererTrack()) {
        pendingTrack = pending
        switchController.begin(targetTrack:"target", nowNanos:0)
    }
    func currentSourceVideoTimeUs() -> UInt64 { sourcePlayheadUs }
    func cancelVideoStallCheck() {}
    func emitSwitchProgress(_ phase: SwitchPhase, track: VideoRendererTrack) { phases.append(phase) }
    func emitDisplayFlush(reason: DecoderFlushReason, trigger: String, droppedFrames: Int, track: VideoRendererTrack) {}
    func performSwap(to next: VideoRendererTrack) {
        swappedPts = next.peekFront()?.timestampUs
        activeTrack = next
        next.setPlaybackActive(true)
        pendingTrack = nil
        switchController.complete()
    }
    func tick(_ pts: UInt64, at nanos: UInt64 = 0) {
        sourcePlayheadUs = pts; nowNanos = nanos
        activeTrack.latestAdmitted = pts+650000
        advancePendingTrackSwapIfNeeded()
    }
    func abort() { abortPendingSwitch(expectedTrialAbort:true) }
RENDERER_MEMBERS
}
var failures = 0
func check(_ ok: Bool, _ label: String) {
    print("\\(ok ? "PASS" : "FAIL"): \\(label)")
    if !ok { failures += 1 }
}
func prepared() -> Probe {
    let p = Probe(); p.pendingTrack!.gop(1000000); p.tick(980000)
    return p
}
let drain = prepared()
drain.tick(1034000,at:54000000)
check(drain.swappedPts == 1000000,"accepted downshift commits after normal reserve drain")

let prune = prepared(), retained = prune.pendingTrack!
retained.gop(1250000)
retained.discardBeforeNewestKeyframe(1300000)
prune.tick(1300000,at:320000000)
check(prune.swappedPts == 1000000,"new arrival cannot prune the accepted keyframe before commit")

let notReady = Probe(); notReady.pendingTrack!.add(1000000,key:true)
notReady.tick(980000)
check(notReady.swappedPts == nil && !notReady.phases.contains(.cutIn),"preparation still requires enough target reserve")

let reset = prepared(); reset.pendingTrack!.flush(); reset.tick(1034000,at:54000000)
check(reset.swappedPts == nil,"reset target cannot commit missing compressed frames")

let stale = prepared(); stale.tick(1300000,at:320000000)
check(stale.swappedPts == nil,"expired target tail cannot be committed")

let expiredKey = prepared(); expiredKey.pendingTrack!.gop(1750000)
expiredKey.tick(1600000,at:620000000)
check(expiredKey.swappedPts == nil,"accepted keyframe beyond the existing freshness window is revalidated")

let boundedTrack = VideoRendererTrack(maxFrames:4)
boundedTrack.add(1000000,key:true); boundedTrack.add(1100000); boundedTrack.add(1200000)
let bounded = Probe(boundedTrack); bounded.tick(980000)
boundedTrack.add(1250000,key:true); boundedTrack.add(1300000)
bounded.tick(1050000,at:70000000)
check(boundedTrack.diagnosticDepth.frames <= 4 && bounded.swappedPts == nil,"pinning cannot bypass capacity eviction or commit an evicted keyframe")

let abort = prepared(), fallback = abort.pendingTrack!
fallback.isRetainedFallback = true; abort.abort()
fallback.gop(1250000); fallback.discardBeforeNewestKeyframe(1300000)
check(fallback.firstKeyframePts == 1250000,"aborting a retained fallback releases the keyframe pin")

let timeout = prepared(); timeout.pendingTrack!.flush(); timeout.tick(1034000,at:4000000000)
timeout.tick(1034000,at:5000000000)
check(timeout.pendingTrack == nil && timeout.phases.contains(.aborted),"re-preparation preserves the original five-second deadline")

let upgrade = Probe(); upgrade.minimumTrialLeadUs = 595000
upgrade.pendingTrack!.gop(1000000); upgrade.pendingTrack!.gop(1250000); upgrade.pendingTrack!.gop(1500000)
upgrade.tick(980000,at:1000000000)
let early = upgrade.swappedPts == nil && !upgrade.phases.contains(.cutIn)
upgrade.tick(980000,at:1600000000); upgrade.tick(1200000,at:1820000000)
check(early && upgrade.swappedPts == 1000000,"upgrade keeps trial dwell but accepted cut-in survives reserve drain")

let active = prepared(); active.tick(1034000,at:54000000)
active.activeTrack.gop(1250000)
active.activeTrack.discardBeforeNewestKeyframe(1300000)
check(active.swappedPts == 1000000 && active.activeTrack.firstKeyframePts == 1000000,
      "late pending-ingest pruning cannot discard the newly active decoder chain")

let ordinary = VideoRendererTrack(); ordinary.gop(1000000); ordinary.gop(1250000)
ordinary.discardBeforeNewestKeyframe(1300000)
check(ordinary.firstKeyframePts == 1250000,"ordinary unpinned pending data still prunes to the newest usable GOP")
exit(failures == 0 ? 0 : 1)
'''.replace('TRACK_MEMBERS','\n'.join(track_members)).replace('RENDERER_MEMBERS','\n'.join(renderer_members))
with tempfile.TemporaryDirectory(prefix='moq-ios-accepted-cutin-') as tmp:
    tmp = Path(tmp)
    (tmp/'main.swift').write_text(harness)
    models = (root/'pipeline/PipelineModels.swift').read_text().split('\nextension PipelineMediaKind',1)[0]
    (tmp/'Models.swift').write_text(models)
    subprocess.run(['swiftc','-module-cache-path',str(tmp/'modules'),
                    str(sdk/'Subscribe/PipelineEvents.swift'),str(root/'pipeline/PipelinePolicies.swift'),
                    str(root/'pipeline/FrameBuffer.swift'),str(root/'pipeline/RenditionSwitchController.swift'),
                    str(tmp/'Models.swift'),str(tmp/'main.swift'),'-o',str(tmp/'test')],check=True)
    raise SystemExit(subprocess.run([str(tmp/'test')]).returncode)
