package com.swmansion.moqkit.subscribe.internal.playback

import com.swmansion.moqkit.subscribe.internal.pipeline.RenditionSwitchResources
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class VideoIngestHandleTest {
    private class Subscription : AutoCloseable {
        private val closed = AtomicBoolean()
        val nativeCloses = AtomicInteger()
        override fun close() { if (closed.compareAndSet(false, true)) nativeCloses.incrementAndGet() }
    }

    @Test fun cancelBeforeCoroutineStartsClosesDemandAndPreservesActive() {
        val executor = Executors.newSingleThreadExecutor()
        val dispatcher = executor.asCoroutineDispatcher()
        val held = CountDownLatch(1); val release = CountDownLatch(1)
        executor.submit { held.countDown(); release.await() }
        assertTrue(held.await(2, TimeUnit.SECONDS))
        try {
            val pendingSub = Subscription(); val activeSub = Subscription()
            val entered = AtomicBoolean()
            val job = CoroutineScope(dispatcher).launch {
                entered.set(true)
                try { error("Cancelled coroutine must not start") } finally { pendingSub.close() }
            }
            val pending = VideoIngestHandle(job, pendingSub)
            val active = VideoIngestHandle(Job(), activeSub)
            val owners = RenditionSwitchResources(active, VideoIngestHandle::cancel)
            owners.begin(pending)
            assertTrue(owners.abort(pending))
            assertEquals(1, pendingSub.nativeCloses.get())
            assertEquals(0, activeSub.nativeCloses.get())
            assertFalse(entered.get())
            release.countDown(); runBlocking { job.join() }
            assertEquals(1, pendingSub.nativeCloses.get())
            owners.close()
            assertEquals(1, activeSub.nativeCloses.get())
        } finally { release.countDown(); dispatcher.close() }
    }

    @Test fun cancellationClosesDemandWhileRunningCoroutineCannotReachFinally() {
        val executor = Executors.newSingleThreadExecutor()
        val dispatcher = executor.asCoroutineDispatcher()
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        val subscription = Subscription()
        try {
            val job = CoroutineScope(dispatcher).launch {
                try { entered.countDown(); release.await() } finally { subscription.close() }
            }
            val handle = VideoIngestHandle(job, subscription)
            assertTrue(entered.await(2, TimeUnit.SECONDS))
            handle.cancel()
            assertEquals(1, subscription.nativeCloses.get())
            assertFalse(job.isCompleted)
            release.countDown(); runBlocking { job.join() }
            assertEquals(1, subscription.nativeCloses.get())
        } finally { release.countDown(); dispatcher.close() }
    }

    @Test fun historicalJobOnlyCancellationCannotCloseUntilBlockedCoroutineResumes() {
        val executor = Executors.newSingleThreadExecutor()
        val dispatcher = executor.asCoroutineDispatcher()
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        val subscription = Subscription()
        try {
            val job = CoroutineScope(dispatcher).launch {
                try { entered.countDown(); release.await() } finally { subscription.close() }
            }
            assertTrue(entered.await(2, TimeUnit.SECONDS))
            // Exact old resource owner's close action: Job::cancel.
            val owners = RenditionSwitchResources<Job>(close = Job::cancel)
            owners.begin(job); assertTrue(owners.abort(job))
            assertEquals(0, subscription.nativeCloses.get())
            assertFalse(job.isCompleted)
            release.countDown(); runBlocking { job.join() }
            assertEquals(1, subscription.nativeCloses.get())
        } finally { release.countDown(); dispatcher.close() }
    }

    @Test fun alreadyCompletedJobClosesAndRetainedFallbackSurvivesAbort() {
        val endedSub = Subscription(); val ended = Job().also { it.complete() }
        VideoIngestHandle(ended, endedSub)
        assertEquals(1, endedSub.nativeCloses.get())
        val retainedSub = Subscription(); val retained = VideoIngestHandle(Job(), retainedSub)
        val owners = RenditionSwitchResources<VideoIngestHandle>(close = VideoIngestHandle::cancel)
        owners.retain(retained); owners.begin(retained); assertTrue(owners.abort(retained))
        assertEquals(0, retainedSub.nativeCloses.get()); assertTrue(retained.isActive)
        owners.close(); assertEquals(1, retainedSub.nativeCloses.get())
    }
}
