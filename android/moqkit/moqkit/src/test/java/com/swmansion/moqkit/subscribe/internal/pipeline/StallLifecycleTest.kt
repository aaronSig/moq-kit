package com.swmansion.moqkit.subscribe.internal.pipeline

import com.swmansion.moqkit.subscribe.PipelineContext
import com.swmansion.moqkit.subscribe.PipelineEvent
import com.swmansion.moqkit.subscribe.PipelineMediaKind
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import org.junit.Assert.*
import org.junit.Test

class StallLifecycleTest {
    private val context = PipelineContext("video/main", PipelineMediaKind.VIDEO, 0)
    private val policy = StallPolicy(arrivalGapUs = 1_000, decodeProgressUs = 1_000,
        renderProgressUs = 1_000, stallDebounceUs = 100)

    @Test fun bufferedRenderingDoesNotStallWhenIngestionPausesButRealStarvationDoes() {
        val monitor = StallMonitor(context, policy)
        monitor.onEvent(PipelineEvent.FrameArrived(context, 0, null, null, 1))
        // Ingestion stopped, but the old rendition is still rendering its buffered frames.
        for (now in listOf(1_050_000L, 1_150_000L, 2_000_000L)) {
            monitor.onEvent(PipelineEvent.FrameRendered(context.copy(timestampNanos = now), 1, now))
            assertTrue("Buffered output is still progressing", monitor.evaluate(now).isEmpty())
        }
        // Once the renderer also runs dry, the same ingestion failure must be detected.
        assertTrue(monitor.evaluate(3_050_000).isEmpty())
        assertEquals(1, monitor.evaluate(3_150_000).filterIsInstance<PipelineEvent.StallStarted>().size)
        monitor.onEvent(PipelineEvent.FrameRendered(context.copy(timestampNanos = 3_200_000), 2, 3_200_000))
        assertEquals(1, monitor.evaluate(3_200_000).filterIsInstance<PipelineEvent.StallEnded>().size)
    }

    @Test fun closingAStalledTrackEndsItsStallAndLateRenderingDoesNotReviveIt() {
        var now = 0L
        val bus = PipelineBus()
        val coordinator = PipelineStallCoordinator(bus, CoroutineScope(SupervisorJob()), policy,
            object : TimeSource { override fun nanoTime() = now })
        val events = mutableListOf<PipelineEvent>()
        val observation = bus.observe { events += it }
        try {
            bus.emit(PipelineEvent.FrameArrived(context, 0, null, null, 1))
            now = 1_050_000; coordinator.evaluate()
            now = 1_150_000; coordinator.evaluate()
            assertEquals(1, events.filterIsInstance<PipelineEvent.StallStarted>().size)
            now = 1_250_000
            bus.emit(PipelineEvent.TransportClosed(context.copy(timestampNanos = now), null))
            assertEquals(1, events.filterIsInstance<PipelineEvent.StallEnded>().size)
            assertEquals(0L, events.filterIsInstance<PipelineEvent.StallEnded>().single().durationMillis)
            bus.emit(PipelineEvent.FrameRendered(context.copy(timestampNanos = now), 1, now))
            now = 3_000_000; coordinator.evaluate()
            now = 3_200_000; coordinator.evaluate()
            assertEquals("Closed rendition must stay retired", 1, events.filterIsInstance<PipelineEvent.StallStarted>().size)
            // Resubscribing to the same catalog name must create a working monitor again.
            bus.emit(PipelineEvent.FrameArrived(context.copy(timestampNanos = now), 2, null, null, 1))
            now = 4_250_000; coordinator.evaluate()
            now = 4_350_000; coordinator.evaluate()
            assertEquals(2, events.filterIsInstance<PipelineEvent.StallStarted>().size)
        } finally { observation.close(); coordinator.close() }
    }
}
