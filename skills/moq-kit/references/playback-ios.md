# Playback — iOS (Swift)

## Session

```swift
let session = Session(url: relayURL)            // actor — all calls await
Task { for await state in session.state { … } } // .idle → .connecting → .connected; .error / .closed
try await session.connect()
// later: await session.close()                 // idempotent; session is dead afterwards
```

- `connect()` is one-shot: reconnecting means a **new** `Session` (throws `SessionError.alreadyConnected`/`alreadyClosed` on reuse). Guard stale async completions across reconnects with a connection token (compare a `UUID` captured before each await).
- Transport failures surface as state (`.error(SessionError)`), not as exceptions from playback calls.

## Broadcast discovery

```swift
let subscription = try await session.subscribe(prefix: "live") // "" = all; throws .alreadySubscribed per duplicate prefix
for await broadcast in subscription.broadcasts { … }     // keep subscription alive; cancel() frees the prefix
```

Requires a connected session; one active subscription per exact prefix. Hold the `Task` driving the `for await` loop — cancelling it stops discovery.

## Catalog track selection

```swift
for await catalog in broadcast.catalogs() {
    let video = catalog.playableVideoTracks
        .max { ($0.config.coded?.height ?? 0) < ($1.config.coded?.height ?? 0) }?.name
    let audio = catalog.playableAudioTracks.first?.name
}
```

Catalog semantics (updates replace, stream end = offline, playable lists) are in SKILL.md. Video-track playability on iOS is based on codec families the renderer recognizes; actual decode support is still decided by AVFoundation at runtime.

## Player

```swift
// @MainActor — construct and drive on the main actor
let player = try Player(catalog: catalog, videoTrackName: video, audioTrackName: audio,
                        targetBuffering: .milliseconds(100), volume: 1.0)
try await player.play()
```

- At least one of `videoTrackName`/`audioTrackName` is required; unknown names throw at init.
- Controls: `pause()` (resumes from live on next `play()`), `stopAll(reason:)` (terminal), `switchTrack(to:)` (video rendition; seamless when both are active), `switchAudioTrack(to:)`, `updateTargetLatency(_:)` (live), `setVolume` / `audioVolume` (clamped 0–1).
- Audio plays via `AVAudioEngine` through the system output. The SDK never configures `AVAudioSession` (its docs claim none is needed) — set a `.playback`-capable category yourself or the mute switch can silence playback.

## Rendering video

`player.videoLayer` is an `AVSampleBufferDisplayLayer`; add it to a layer hierarchy and size it yourself. Nothing renders until it's in the tree and `play()` has run.

```swift
final class VideoContainerView: UIView {
    private var displayLayer: AVSampleBufferDisplayLayer?
    func setDisplayLayer(_ layer: AVSampleBufferDisplayLayer?) {
        guard layer !== displayLayer else { return }
        displayLayer?.removeFromSuperlayer()
        displayLayer = layer
        if let layer { self.layer.addSublayer(layer) }
        setNeedsLayout()
    }
    override func layoutSubviews() { super.layoutSubviews(); displayLayer?.frame = bounds }
}

struct VideoLayerView: UIViewRepresentable {
    let videoLayer: AVSampleBufferDisplayLayer?
    func makeUIView(context: Context) -> VideoContainerView { VideoContainerView() }
    func updateUIView(_ view: VideoContainerView, context: Context) { view.setDisplayLayer(videoLayer) }
    static func dismantleUIView(_ view: VideoContainerView, coordinator: ()) { view.setDisplayLayer(nil) }
}
```

Moving playback to another screen (e.g. fullscreen) reuses the **same** layer — force the representable to re-add it (the demo bumps an `.id(generation)`).

## Events, stats, diagnostics

Channel semantics are in SKILL.md; the iOS wiring:

```swift
let events = player.subscribeEvents { event in … }   // BEFORE play(); RETAIN the subscription
let stats  = player.subscribeStats { stats in … }    // first push deferred until real data
Task { for await event in player.diagnostics() { … } } // bounded (256) per call, non-replayed
```

Both `subscribeEvents` and `subscribeStats` return a `PlayerEventSubscription` that cancels itself on deinit — store it or events silently stop. Listeners are `@MainActor`. A synchronous `player.stats` snapshot property also exists.

## Lifecycle checklist

1. Create `Session` → observe state → `connect()`.
2. `subscribe(prefix)` → per broadcast, observe `catalogs()`; catalog stream ending = broadcast offline.
3. On the main actor: create `Player` → subscribe events → attach `videoLayer` → `play()`.
4. Teardown in order: cancel observation tasks → `await player.stopAll()` → cancel the broadcast subscription → `await session.close()`.

Keep strong references throughout: `Session`, `BroadcastSubscription`, `Player`, and every `PlayerEventSubscription`; the `Task` handles driving `for await` loops are the lifetime of those streams.

### Video-only stall recovery

A video-driven clock pauses when presentation coverage runs out. Replacement
media must be admitted while that clock is stalled, then the clock resumes at
the new sample timestamp. Healthy video remains paced normally; video recovery
does not re-anchor an audio-driven clock. This prevents a permanent wait after
a live source advances during a video-only stall.

### Delivery priority

`MediaTrackRequest` accepts an optional `priority` (0–255; larger is earlier).
Raw requests retain priority 0. Player audio uses 80 and video uses 60 at up to
360p, 55 at up to 540p, and 50 above that. This preserves delivery of the lower
rendition during an upgrade on the lab ladder. It is a resolution-based policy,
not a bandwidth estimator or a general ordering of same-resolution variants.
Shared requests for an existing track keep the first subscription settings.

### Aligned rendition timestamps

The player calibrates the audio/video timestamp offset once both timelines are
available. Replacing a video rendition preserves that calibration: cached media
behind the current playhead is backlog, not a new source clock. Initial sources
with separate audio/video timestamp domains still receive their fixed offset.

### Rendition handoff ownership

`Player.hasPendingVideoSwitch` and `videoBufferedAhead` expose pending ownership
and admitted media lead. A selection request is not a completed switch. Pending
video prepares on the existing source clock, retains its accepted keyframe and
preroll, and is rechecked even when the old compressed queue is empty. Submitted
old media must drain before the replacement becomes authoritative.

Upgrades require preparation reserve and a bounded trial; a brief grouped-arrival
dip has a grace period, while critically low reserve cancels immediately. A failed
trial preserves the playing track. These policies improve lifecycle correctness;
they do not establish a maximum downshift/recovery time under arbitrary networks.

`warmFallbackVideoTrackName` is an optional experimental retained subscription,
with `warmFallbackBufferedAhead`, `warmFallbackIsReceiving` and
`warmFallbackReuseCount` observations. It defaults to nil and adds network demand
when enabled. Leave it disabled in ordinary playback and bandwidth comparisons.

### Cancelling a trial

Aborting an ordinary pending rendition closes its network subscription before
waiting for UI/actor or coroutine cleanup. Cancellation targets the captured
trial, so a late callback cannot close a newer owner. An explicitly retained
fallback keeps its demand until teardown. Coroutine cancellation before startup
also closes the subscription. These are ownership guarantees, not wire delivery
or physical continuity measurements.
