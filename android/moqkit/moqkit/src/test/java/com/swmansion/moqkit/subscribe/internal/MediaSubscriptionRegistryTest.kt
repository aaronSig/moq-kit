package com.swmansion.moqkit.subscribe.internal

import com.swmansion.moqkit.subscribe.MediaContainer
import com.swmansion.moqkit.subscribe.MediaTrackRequest
import com.swmansion.moqkit.subscribe.LiveMediaSubscriptionFactory
import dev.moq.BroadcastConsumer
import dev.moq.MediaConsumer
import dev.moq.Subscription
import uniffi.moq.NoHandle
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.firstOrNull
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test
import uniffi.moq.MoqContainer
import dev.moq.MediaFrame as NativeMediaFrame
import java.time.Duration

class MediaSubscriptionRegistryTest {
    @Test
    fun priorityReachesTheNativeSubscription() {
        val consumer = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(consumer)
        val registry = MediaSubscriptionRegistry(source)
        val track = registry.subscribeMedia(MediaTrackRequest(name = "video", container = MediaContainer.Legacy, priority = 60u))
        assertEquals(60.toUByte(), source.requests.single().priority)
        track.close()
        registry.close()
    }

    @Test
    fun subscribersShareOneUpstreamAndReceiveTheSameFrames() = runBlocking {
        val consumer = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(consumer)
        val registry = MediaSubscriptionRegistry(source)

        val first = registry.subscribeMedia(request(targetBufferingMs = 100))
        val second = registry.subscribeMedia(request(targetBufferingMs = 250))

        val firstFrame = async { first.frames.firstOrNull() }
        val secondFrame = async { second.frames.firstOrNull() }
        consumer.yield(frame(timestampUs = 42uL))

        assertEquals(42L, withTimeout(1_000) { firstFrame.await()?.timestampUs })
        assertEquals(42L, withTimeout(1_000) { secondFrame.await()?.timestampUs })
        assertEquals(1, source.requests.size)
        assertEquals(100uL, source.requests.single().maxLatencyMs)

        first.close()
        second.close()
        registry.close()
    }

    @Test
    fun closingOneSubscriberKeepsOtherSubscriberActive() = runBlocking {
        val consumer = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(consumer)
        val registry = MediaSubscriptionRegistry(source)

        val first = registry.subscribeMedia(request())
        val second = registry.subscribeMedia(request())

        first.close()
        assertEquals(0, consumer.cancelCallCount)

        val secondFrame = async { second.frames.firstOrNull() }
        consumer.yield(frame(timestampUs = 7uL))

        assertEquals(7L, withTimeout(1_000) { secondFrame.await()?.timestampUs })
        assertEquals(1, source.requests.size)
        assertEquals(1, consumer.cancelCallCount)

        second.close()
        assertEquals(1, consumer.cancelCallCount)
        registry.close()
    }

    @Test
    fun lastSubscriberCloseCancelsAndEvictsUpstream() = runBlocking {
        val firstConsumer = FakeMediaConsumer()
        val secondConsumer = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(firstConsumer, secondConsumer)
        val registry = MediaSubscriptionRegistry(source)

        val first = registry.subscribeMedia(request())
        first.close()

        assertEquals(1, firstConsumer.cancelCallCount)
        assertEquals(1, source.requests.size)
        assertEquals(0, registry.activeSubscriptionCount)

        val second = registry.subscribeMedia(request())
        second.close()

        assertEquals(1, secondConsumer.cancelCallCount)
        assertEquals(2, source.requests.size)
        assertEquals(0, registry.activeSubscriptionCount)

        registry.close()
    }

    @Test
    fun upstreamEndFinishesAllSubscribersAndEvictsUpstream() = runBlocking {
        val consumer = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(consumer)
        val registry = MediaSubscriptionRegistry(source)

        val first = registry.subscribeMedia(request())
        val second = registry.subscribeMedia(request())
        val firstFrame = async { first.frames.firstOrNull() }
        val secondFrame = async { second.frames.firstOrNull() }

        consumer.finish()

        assertNull(withTimeout(1_000) { firstFrame.await() })
        assertNull(withTimeout(1_000) { secondFrame.await() })
        assertEquals(0, consumer.cancelCallCount)
        assertEquals(0, registry.activeSubscriptionCount)

        registry.close()
    }

    @Test
    fun upstreamErrorFinishesAllSubscribersWithError() = runBlocking {
        val consumer = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(consumer)
        val registry = MediaSubscriptionRegistry(source)

        val first = registry.subscribeMedia(request())
        val second = registry.subscribeMedia(request())
        val firstError = async { failureFrom { first.frames.firstOrNull() } }
        val secondError = async { failureFrom { second.frames.firstOrNull() } }

        consumer.fail(MediaSubscriptionTestError)

        assertSame(MediaSubscriptionTestError, withTimeout(1_000) { firstError.await() })
        assertSame(MediaSubscriptionTestError, withTimeout(1_000) { secondError.await() })
        assertEquals(0, registry.activeSubscriptionCount)

        registry.close()
    }

    @Test
    fun factoryReceivesFreshDemandAndLegacyDemandStillUsesOrdinaryApi() = runBlocking {
        val frame = frame(42uL)
        val native = object : MediaConsumer(NoHandle) {
            override suspend fun next() = frame
        }
        var ordinaryCalls = 0
        val broadcast = object : BroadcastConsumer(NoHandle) {
            override suspend fun subscribeMedia(name: String, container: MoqContainer, subscription: Subscription?): MediaConsumer {
                ordinaryCalls++
                return native
            }
        }
        var freshCalls = 0
        val source = UniFFIMediaSubscriptionSource(consumerProvider = { broadcast },
            liveMediaSubscriptionFactory = LiveMediaSubscriptionFactory { owner, name, container, settings ->
                assertSame(broadcast, owner)
                assertEquals("video", name)
                assertSame(MoqContainer.Legacy, container)
                assertEquals(700uL, settings.latencyMaxMs)
                assertEquals(60.toUByte(), settings.priority)
                freshCalls++
                native
            })
        assertSame(frame, source.subscribeMedia("video", MoqContainer.Legacy, 700uL, 60u, true).next())
        assertEquals(1, freshCalls)
        assertEquals(0, ordinaryCalls)
        assertSame(frame, source.subscribeMedia("video", MoqContainer.Legacy, 700uL, 60u, false).next())
        assertEquals(1, freshCalls)
        assertEquals(1, ordinaryCalls)
    }

    @Test
    fun freshAndCachedReadersNeverShareAnUpstream() {
        val cached = FakeMediaConsumer()
        val fresh = FakeMediaConsumer()
        val source = FakeMediaSubscriptionSource(cached, fresh)
        val registry = MediaSubscriptionRegistry(source)
        val historical = registry.subscribeMedia(request())
        val live = registry.subscribeMedia(request().copy(startAtLiveEdge = true))
        val livePeer = registry.subscribeMedia(request().copy(startAtLiveEdge = true))
        assertEquals(listOf(false, true), source.requests.map { it.startAtLiveEdge })
        historical.close()
        assertEquals(1, cached.cancelCallCount)
        assertEquals(0, fresh.cancelCallCount)
        live.close()
        assertEquals(0, fresh.cancelCallCount)
        livePeer.close()
        assertEquals(1, fresh.cancelCallCount)
        registry.close()
    }

    @Test
    fun unsupportedExplicitFreshRequestFailsBeforeNativeDemand() {
        var accessed = false
        val source = UniFFIMediaSubscriptionSource(consumerProvider = { accessed = true; error("No native demand expected") })
        val registry = MediaSubscriptionRegistry(source)
        val failure = runCatching { registry.subscribeMedia(request().copy(startAtLiveEdge = true)) }.exceptionOrNull()
        org.junit.Assert.assertTrue(failure is IllegalStateException)
        org.junit.Assert.assertFalse(accessed)
        assertEquals(0, registry.activeSubscriptionCount)
        registry.close()
    }

    private fun request(targetBufferingMs: Long = 100): MediaTrackRequest =
        MediaTrackRequest(
            name = "audio",
            container = MediaContainer.Legacy,
            targetBuffering = Duration.ofMillis(targetBufferingMs),
        )

    private fun frame(timestampUs: ULong): NativeMediaFrame =
        NativeMediaFrame(
            payload = byteArrayOf(0x01, 0x02, 0x03),
            timestampUs = timestampUs,
            keyframe = true,
        )

    private suspend fun failureFrom(block: suspend () -> Unit): Throwable? =
        try {
            block()
            null
        } catch (t: Throwable) {
            t
        }
}

private class FakeMediaSubscriptionSource(
    vararg consumers: FakeMediaConsumer,
) : MediaSubscriptionSource {
    data class Request(
        val name: String,
        val container: MoqContainer,
        val maxLatencyMs: ULong,
        val priority: UByte,
        val startAtLiveEdge: Boolean,
    )

    private val consumers = ArrayDeque(consumers.toList())
    val requests = mutableListOf<Request>()

    override fun subscribeMedia(
        name: String,
        container: MoqContainer,
        maxLatencyMs: ULong,
        priority: UByte,
        startAtLiveEdge: Boolean,
    ): MediaConsumerHandle {
        requests += Request(name, container, maxLatencyMs, priority, startAtLiveEdge)
        return consumers.removeFirst()
    }
}

private class FakeMediaConsumer : MediaConsumerHandle {
    private val results = Channel<Result<NativeMediaFrame?>>(Channel.UNLIMITED)
    var cancelCallCount = 0
        private set
    var closeCallCount = 0
        private set

    override suspend fun next(): NativeMediaFrame? =
        results.receive().getOrThrow()

    override fun cancel() {
        cancelCallCount += 1
        results.trySend(Result.success(null))
    }

    override fun close() {
        closeCallCount += 1
    }

    fun yield(frame: NativeMediaFrame) {
        results.trySend(Result.success(frame))
    }

    fun finish() {
        results.trySend(Result.success(null))
    }

    fun fail(error: Throwable) {
        results.trySend(Result.failure(error))
    }
}

private object MediaSubscriptionTestError : Exception("upstream failed")
