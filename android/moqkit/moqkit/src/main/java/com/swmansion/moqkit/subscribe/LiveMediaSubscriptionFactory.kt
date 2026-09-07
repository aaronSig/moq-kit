package com.swmansion.moqkit.subscribe

import dev.moq.BroadcastConsumer
import dev.moq.MediaConsumer
import dev.moq.Subscription
import uniffi.moq.MoqContainer

/** Optional bridge to a native runtime supporting fresh live media readers.
 *
 * The factory must skip groups already cached before this request without changing
 * other readers, wire priority, or latency. It owns the returned consumer until
 * the SDK closes it. The supplied broadcast remains owned by the Session.
 *
 * Without this capability, catalog playback retains ordinary cached subscriptions.
 * Explicit [MediaTrackRequest.startAtLiveEdge] requests require this capability.
 */
fun interface LiveMediaSubscriptionFactory {
    suspend fun subscribe(
        broadcast: BroadcastConsumer,
        name: String,
        container: MoqContainer,
        subscription: Subscription,
    ): MediaConsumer
}
