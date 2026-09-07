#!/usr/bin/env python3
"""Run the actual abort callback's cancellation sections with MainActor held.

Extracts the production callback before/inside its actor hop and the real locked
MediaFrameStream. Rendering and pipeline bookkeeping use observable doubles.
This proves cancellation scheduling, not wire delivery or physical continuity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

lab = Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--source-root', type=Path, default=lab)
p.add_argument('--output', type=Path)
a = p.parse_args()
if a.output is None: a.output = Path(tempfile.mkdtemp(prefix='moq-prompt-cancel-'))/'result'
a.output.mkdir(exist_ok=False, parents=True)
base = a.source_root/'ios/Sources/MoQKit/Subscribe'
pipeline = (base/'internal/playback/PlaybackPipeline.swift').read_text()
track = (base/'MediaTrack.swift').read_text()
registry = (base/'internal/MediaSubscriptionRegistry.swift').read_text()
callback = pipeline.split('            onAborted: { [weak self] expectedTrialAbort in\n', 1)[1]
immediate, deferred = callback.split('                Task { @MainActor [weak self] in\n', 1)
deferred = deferred.split('                    let restored =', 1)[0]
stream = registry.split('// MARK: - Media Frame Stream', 1)[1].split('// MARK: - Media Subscription Registry', 1)[0]
cancel = ''
if '    func cancelUpstream() {' in track:
    cancel = '    func cancelUpstream() {'+track.split('    func cancelUpstream() {', 1)[1].split('\n    }', 1)[0]+'\n    }'
harness = r'''import Foundation
struct MediaFrame {}
final class UnfairLock: @unchecked Sendable {
    private let lock = NSLock()
    func withLock<T>(_ work: () -> T) -> T { lock.lock(); defer { lock.unlock() }; return work() }
}
STREAM
final class Counter: @unchecked Sendable {
    private let lock = UnfairLock(); private var value = 0
    var count: Int { lock.withLock { value } }
    func increment() { lock.withLock { value += 1 } }
}
final class MediaTrack: @unchecked Sendable {
    let nativeCloses = Counter()
    private var media: MediaFrameStream!
    init() {
        let counter = nativeCloses
        media = MediaFrameStream(frames: AsyncThrowingStream { _ in }, close: { counter.increment() })
    }
    CANCEL
    func close() { media.close() }
}
final class RendererTrack: @unchecked Sendable {
    let isRetainedFallback: Bool
    init(_ retained: Bool) { isRetainedFallback = retained }
}
final class Token: @unchecked Sendable {}
@MainActor final class Pipeline {
    var pendingVideoCleanup: Token?
    var videoTask: Task<Void, Never>?
    var videoSubscription: MediaTrack?
    var warmFallbackTrack: RendererTrack?
    func callback(_ newRendererTrack: RendererTrack, _ newSub: MediaTrack,
                  _ oldHandle: Token, _ deferredDone: DispatchSemaphore) -> @Sendable (Bool) -> Void {
        return { [weak self] expectedTrialAbort in
IMMEDIATE
            Task { @MainActor [weak self] in
                defer { deferredDone.signal() }
DEFERRED
            }
        }
    }
}
@main struct Check {
    @MainActor static func main() async {
        var failures = 0
        func check(_ ok: Bool, _ label: String) {
            print("\(ok ? "PASS" : "FAIL"): \(label)")
            if !ok { failures += 1 }
        }
        for scenario in ["ordinary", "retained", "newer-owner", "owner-gone"] {
            let pending = MediaTrack(), active = MediaTrack(), newer = MediaTrack()
            let renderer = RendererTrack(scenario == "retained"), token = Token()
            var owner: Pipeline? = Pipeline()
            owner!.pendingVideoCleanup = token; owner!.videoSubscription = pending
            if scenario == "retained" { owner!.warmFallbackTrack = renderer }
            let done = DispatchSemaphore(value: 0), deferredDone = DispatchSemaphore(value: 0)
            let callback = owner!.callback(renderer, pending, token, deferredDone)
            if scenario == "newer-owner" {
                owner!.pendingVideoCleanup = Token(); owner!.videoSubscription = newer
            }
            if scenario == "owner-gone" { owner = nil }
            DispatchQueue.global().async { callback(true); done.signal() }
            // Hold MainActor until the renderer-side callback has returned.
            check(done.wait(timeout: .now()+2) == .success, "\(scenario): callback returns with actor held")
            check(pending.nativeCloses.count == (scenario == "retained" ? 0 : 1),
                  "\(scenario): trial cancellation does not wait for MainActor")
            check(active.nativeCloses.count == 0 && newer.nativeCloses.count == 0,
                  "\(scenario): other subscriptions are untouched")
            let deferredFinished = await Task.detached { deferredDone.wait(timeout: .now()+2) == .success }.value
            check(deferredFinished, "\(scenario): deferred cleanup completes after actor release")
            check(pending.nativeCloses.count == (scenario == "retained" ? 0 : 1),
                  "\(scenario): upstream closes at most once and retained demand survives")
            pending.close(); active.close(); newer.close()
            _ = owner
        }
        exit(failures == 0 ? 0 : 1)
    }
}
'''.replace('STREAM', stream).replace('CANCEL', cancel).replace('IMMEDIATE', immediate).replace('DEFERRED', deferred)
source = a.output/'main.swift'
source.write_text(harness)
with (a.output/'compile.log').open('w') as log:
    subprocess.run(['swiftc', '-parse-as-library', '-module-cache-path', str(a.output/'modules'),
                    str(source), '-o', str(a.output/'checks')], check=True, stdout=log, stderr=subprocess.STDOUT)
with (a.output/'result.log').open('w') as log:
    result = subprocess.run([str(a.output/'checks')], stdout=log, stderr=subprocess.STDOUT, timeout=15)
(a.output/'result.json').write_text(json.dumps({'exitCode': result.returncode,
    'pipelineSha256': hashlib.sha256(pipeline.encode()).hexdigest(),
    'trackSha256': hashlib.sha256(track.encode()).hexdigest(),
    'registrySha256': hashlib.sha256(registry.encode()).hexdigest(),
    'scope': 'Extracted real cancellation sections and locked stream; controlled actor blockage, no wire or display model'}, indent=2)+'\n')
print((a.output/'result.log').read_text())
raise SystemExit(result.returncode)
