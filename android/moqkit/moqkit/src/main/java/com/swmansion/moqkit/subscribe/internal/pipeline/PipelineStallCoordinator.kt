package com.swmansion.moqkit.subscribe.internal.pipeline

import com.swmansion.moqkit.subscribe.PipelineContext
import com.swmansion.moqkit.subscribe.PipelineEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/** Feeds one pure [StallMonitor] per active track and periodically evaluates silence. */
internal class PipelineStallCoordinator(
    private val bus: PipelineBus,
    scope: CoroutineScope,
    private val policy: StallPolicy = PipelinePolicies.stall,
    private val timeSource: TimeSource = MonotonicTimeSource,
) : AutoCloseable {
    private val lock = Any()
    private val monitors = mutableMapOf<TrackKey, StallMonitor>()
    private val closedTracks = mutableSetOf<TrackKey>()
    private val observation = bus.observe(::onEvent)
    private val scope = scope
    private var evaluationJob: Job? = null

    fun start() {
        if (evaluationJob?.isActive == true) return
        evaluationJob = scope.launch {
            while (isActive) {
                delay(EVALUATION_INTERVAL_MILLIS)
                evaluate()
            }
        }
    }

    fun stop() {
        evaluationJob?.cancel()
        evaluationJob = null
        synchronized(lock) { monitors.clear(); closedTracks.clear() }
    }

    override fun close() {
        observation.close()
        stop()
    }

    internal fun evaluate() {
        val now = timeSource.nanoTime()
        val events = synchronized(lock) {
            monitors.values.flatMap { it.evaluate(now) }
        }
        events.forEach(bus::emit)
    }

    private fun onEvent(event: PipelineEvent) {
        if (event is PipelineEvent.StallStarted || event is PipelineEvent.StallEnded) return
        val key = event.context.trackKey
        val completed = synchronized(lock) {
            if (event is PipelineEvent.TransportClosed) {
                closedTracks.add(key)
                return@synchronized monitors.remove(key)?.finish(event.context.timestampNanos) ?: emptyList()
            }
            // A renderer may drain queued output after its network subscription closes.
            // Only actual new ingestion can reopen a retired catalog track.
            if (event is PipelineEvent.FrameArrived) closedTracks.remove(key)
            if (key !in closedTracks) {
                monitors.getOrPut(key) { StallMonitor(event.context.stableContext, policy) }.onEvent(event)
            }
            emptyList()
        }
        completed.forEach(bus::emit)
    }

    private val PipelineContext.trackKey: TrackKey
        get() = TrackKey(trackId, mediaKind)

    private val PipelineContext.stableContext: PipelineContext
        get() = copy(timestampNanos = 0L)

    private data class TrackKey(
        val trackId: String,
        val mediaKind: com.swmansion.moqkit.subscribe.PipelineMediaKind,
    )

    private companion object {
        const val EVALUATION_INTERVAL_MILLIS = 50L
    }
}
