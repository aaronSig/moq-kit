# Playback — Android (Kotlin)

## Session

```kotlin
val session = Session(url = relayURL, parentScope = viewModelScope)
scope.launch { session.state.collect { … } }  // StateFlow: Idle/Connecting/Connected/Error(message)/Closed
session.connect()                             // the only suspend call
// later: session.close()                     // synchronous, idempotent
```

- `connect()` is one-shot: reconnecting means a **new** `Session`. Guard stale async completions across reconnects with a connection token.
- `parentScope` puts all session work under a child `SupervisorJob` of that scope — cancelling `viewModelScope` tears the session down. On connection loss the session sets `Error` and immediately closes itself, milliseconds apart; treat a `Closed` you didn't request as the failure signal, since the conflated `StateFlow` can skip `Error`. Handle `Error(message)` too rather than ignoring it — a plain `collect` does observe it in practice, and the message (e.g. `Session ended: uniffi.moq.MoqException$Protocol: transport: connection closed`) is the only description of the failure you get anywhere.
- Transport failures surface through session state, never as exceptions from playback calls.

## Broadcast discovery

```kotlin
val subscription = session.subscribe(prefix = "live") // not suspend; requires Connected; "" = all
subscription.broadcasts.collect { broadcast -> … }    // cold, SINGLE collector
```

- One active subscription per exact prefix. `subscribe()`, `publish()`, `play()` are **not** suspend — easy to over-wrap.
- `BroadcastSubscription` and each emitted `Broadcast` are `AutoCloseable` and hold ref-counted native handles — `close()` them (the demo does it in `finally`).

## Catalog track selection

```kotlin
broadcast.catalogs().collect { catalog ->
    val video = catalog.playableVideoTracks.maxByOrNull { it.config.coded?.height ?: 0u }?.name
    val audio = catalog.playableAudioTracks.firstOrNull()?.name
}
```

Catalog semantics (updates replace, stream end = offline, playable lists) are in SKILL.md. Wrap the collect in `try`/`catch`: the stream usually ends by throwing `MoqException$Mux` when the producer goes away, so the offline path needs to run from both `catch` and normal completion — see `observeCatalogs` in the demo's `PlayerDemoViewModel`.

## Player

```kotlin
val player = Player(catalog, video, audio,
                    targetBuffering = Duration.ofMillis(100),
                    parentScope = viewModelScope, volume = 1f) // parentScope has NO default
player.play() // not suspend; fine to call before a surface exists
```

- At least one of `videoTrackName`/`audioTrackName` is required; unknown names throw `IllegalArgumentException` at construction; undecodable selected tracks throw `UnsupportedCodecException` from `play()` — it extends `IllegalArgumentException`, so catch it first.
- Controls: `pause()` (resumes from live on next `play()`), `stopAll(reason = …)` (tears down) and `close()` (terminal — only `close()` blocks a later `play()`; treat both as end-of-life), `switchTrack(trackName)` (video rendition; `null` disables video), `switchAudioTrack(trackName)`, `updateTargetLatency(latency)` (live), `setVolume` (clamped 0–1).
- Decodes via MediaCodec, renders audio via AudioTrack; video needs a `Surface` (below).

## Rendering video

No view class is provided — feed `setSurface(Surface?)` from a `SurfaceView`:

```kotlin
AndroidView(factory = { ctx ->
    SurfaceView(ctx).apply {
        holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(h: SurfaceHolder) = player.setSurface(h.surface)
            override fun surfaceChanged(h: SurfaceHolder, f: Int, w: Int, hh: Int) {}
            override fun surfaceDestroyed(h: SurfaceHolder) = player.setSurface(null)
        })
    }
})
```

Set the surface on `surfaceCreated`, **null it on `surfaceDestroyed`** (a stale surface means no video or crashes), and only re-apply a surface that `isValid`. `setSurface(null)` keeps audio running while video waits; `play()` before any surface is fine.

## Events, stats, diagnostics

Channel semantics are in SKILL.md; the Android wiring:

```kotlin
scope.launch(start = CoroutineStart.UNDISPATCHED) { player.events.collect { … } } // BEFORE play()
scope.launch { player.statsUpdates.collect { stats -> … } }                       // ~1 s cadence; also player.stats
// PlaybackStats fields are nullable until data flows: videoFps: Double?,
// videoLatency/audioLatency: Duration?, videoBitrateKbps: Double?, …
scope.launch { player.diagnostics().collect { … } }                               // same shared flow every call; one 256-slot drop-oldest buffer
```

`events` and `statsUpdates` are `SharedFlow`s with no replay — `UNDISPATCHED` collection before `play()` is how the demo avoids missing `track.ready`/`playback.start` (`player.init` fires during construction and is unobservable).

## Lifecycle checklist

1. Create `Session(url, parentScope)` → observe state → `connect()`.
2. `subscribe(prefix)` → per broadcast, collect `catalogs()`; catalog stream ending = broadcast offline → `broadcast.close()`.
3. Create `Player(…, parentScope)` → launch events/stats collectors (`UNDISPATCHED`) → wire `SurfaceHolder.Callback` → `play()`.
4. Teardown in order: cancel collector jobs → `player.close()` → `close()` broadcasts and the subscription → `session.close()`.

Everything `AutoCloseable` holds a ref-counted native broadcast handle — leaking one keeps the broadcast open.

### Delivery priority

`MediaTrackRequest` accepts an optional `priority` (0–255; larger is earlier).
Raw requests retain priority 0. Player audio uses 80 and video uses 65 at up to 240p, 60 at up to
360p, 55 at up to 540p, and 50 above that. This preserves delivery of the lower
rendition during an upgrade on the lab ladder. It is a resolution-based policy,
not a bandwidth estimator or a general ordering of same-resolution variants.
Shared requests for an existing track keep the first subscription settings.

### Rendition handoff ownership

`Player` exposes pending-switch state and admitted video lead so an application
can distinguish a request from a completed handoff. The pipeline owns active and
pending ingest resources independently. A downshift stops obsolete input while
queued decoder output drains; duplicate timestamps retain submission-order
metadata and old/new overlap cannot be presented backwards.

Upgrade preparation is bounded and cancels when the playing track loses its
reserve. The optional retained fallback track remains disabled unless explicitly
requested; enabling it adds continuous network demand. These policies are a lab
baseline, not a guarantee of interruption-free switching or prompt recovery on
all networks. Statistics include zero-frame windows so a frozen renderer is not
reported as the last healthy frame rate.

### Stall lifecycle

Retiring a track closes any active stall interval owned by that track. Audio
and video stalls remain separate, and a late event from retired video cannot
close or restart the replacement track's interval. Stopping the player retires
its active intervals once instead of leaving stale recovery state behind.

### Cancelling a trial

Aborting an ordinary pending rendition closes its network subscription before
waiting for UI/actor or coroutine cleanup. Cancellation targets the captured
trial, so a late callback cannot close a newer owner. An explicitly retained
fallback keeps its demand until teardown. Coroutine cancellation before startup
also closes the subscription. These are ownership guarantees, not wire delivery
or physical continuity measurements.

### Receive-dispatch timing

Video `FrameArrived.nativeReadyNanos` optionally records the Kotlin wrapper time
after the native read returns and before pipeline coroutine dispatch. Compare it
with the event time to identify dispatch delay. It is not packet arrival, native
container readiness or a cross-device clock. Application-created frames report
no native provenance; the observation does not change frame equality or payload.
