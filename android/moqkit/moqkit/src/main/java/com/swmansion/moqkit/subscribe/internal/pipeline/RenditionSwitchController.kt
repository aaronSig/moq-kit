package com.swmansion.moqkit.subscribe.internal.pipeline

internal sealed interface SwitchState {
    data object Steady : SwitchState
    data class Preparing(val targetTrack: String) : SwitchState
    data class CuttingIn(val targetTrack: String, val keyframePtsUs: Long) : SwitchState
    data class FlushSwap(val targetTrack: String) : SwitchState
}

internal sealed interface SwitchDecision {
    data object Wait : SwitchDecision
    data class CutIn(val keyframePtsUs: Long) : SwitchDecision
    data object FlushSwap : SwitchDecision
    data class Abort(val targetTrack: String) : SwitchDecision
}

/** Pure rendition-switch state machine; the renderer only executes its decisions. */
internal class RenditionSwitchController(
    private val policy: SwitchPolicy,
) {
    private var reservePressureStartedNs: Long? = null

    var state: SwitchState = SwitchState.Steady
        private set

    fun begin(targetTrack: String) {
        require(targetTrack.isNotBlank()) { "target track must not be blank" }
        reservePressureStartedNs = null
        state = SwitchState.Preparing(targetTrack)
    }

    fun canPromoteUpgrade(elapsedNs: Long, bufferedAheadUs: Long, requiredAheadUs: Long): Boolean =
        elapsedNs >= 1_500_000_000L && bufferedAheadUs >= requiredAheadUs

    fun shouldAbandonUpgrade(
        activeAheadUs: Long,
        minimumActiveLeadUs: Long,
        nowNs: Long = System.nanoTime(),
    ): Boolean {
        if (minimumActiveLeadUs <= 0 || activeAheadUs >= minimumActiveLeadUs) {
            reservePressureStartedNs = null
            return false
        }
        // Grouped delivery briefly crosses the normal watermark. Give it the
        // same bounded replenishment grace as iOS, but cancel immediately when
        // the playing picture has less than 200 ms left (or a smaller target).
        if (activeAheadUs < minOf(minimumActiveLeadUs, 200_000L)) return true
        val started = reservePressureStartedNs ?: nowNs
        reservePressureStartedNs = started
        return nowNs >= started && nowNs - started >= 200_000_000L
    }

    fun shouldSuppressOverlap(isNewTrack: Boolean, ptsUs: Long, oldLastFedUs: Long): Boolean =
        isNewTrack && ptsUs <= oldLastFedUs

    fun onKeyframeAvailable(lastFedPtsUs: Long, keyframePtsUs: Long): SwitchDecision {
        val preparing = state as? SwitchState.Preparing ?: return SwitchDecision.Wait
        val gapUs = positiveDifference(lastFedPtsUs, keyframePtsUs)
        return if (gapUs > policy.flushThresholdUs) {
            state = SwitchState.FlushSwap(preparing.targetTrack)
            SwitchDecision.FlushSwap
        } else {
            state = SwitchState.CuttingIn(preparing.targetTrack, keyframePtsUs)
            SwitchDecision.Wait
        }
    }

    fun onActiveProgress(lastFedPtsUs: Long): SwitchDecision = when (val current = state) {
        is SwitchState.CuttingIn -> if (lastFedPtsUs >= current.keyframePtsUs) {
            SwitchDecision.CutIn(current.keyframePtsUs)
        } else {
            SwitchDecision.Wait
        }
        is SwitchState.FlushSwap -> SwitchDecision.FlushSwap
        SwitchState.Steady,
        is SwitchState.Preparing -> SwitchDecision.Wait
    }

    /** The cancelled active track cannot advance to a future keyframe. Decode the
     * pending track now; the existing presentation clock still schedules its output. */
    fun onActiveExhausted(): SwitchDecision = when (val current = state) {
        is SwitchState.CuttingIn -> SwitchDecision.CutIn(current.keyframePtsUs)
        is SwitchState.FlushSwap -> SwitchDecision.FlushSwap
        else -> SwitchDecision.Wait
    }

    fun onTimeout(): SwitchDecision {
        val target = when (val current = state) {
            is SwitchState.Preparing -> current.targetTrack
            is SwitchState.CuttingIn -> current.targetTrack
            else -> return SwitchDecision.Wait
        }
        state = SwitchState.Steady
        return SwitchDecision.Abort(target)
    }

    fun shouldDiscardPendingDelta(lastFedPtsUs: Long, framePtsUs: Long): Boolean =
        positiveDifference(lastFedPtsUs, framePtsUs) > policy.cutInWindowUs

    fun complete() {
        reservePressureStartedNs = null
        state = SwitchState.Steady
    }

    private fun positiveDifference(left: Long, right: Long): Long {
        if (left <= right) return 0L
        return try {
            Math.subtractExact(left, right)
        } catch (_: ArithmeticException) {
            Long.MAX_VALUE
        }
    }
}

/** Decoder output can contain the same PTS from both sides of an overlapping
 * switch. Keep their metadata in decode submission order, rather than replacing
 * the old frame's identity with the new rendition's identity. */
internal class DecoderMetadataQueue<T> {
    private val values = mutableMapOf<Long,ArrayDeque<T>>()
    var size = 0
        private set
    operator fun set(pts: Long, value: T) { values.getOrPut(pts) { ArrayDeque() }.addLast(value); size++ }
    fun remove(pts: Long): T? {
        val queue = values[pts] ?: return null
        val value = queue.removeFirst(); size--
        if (queue.isEmpty()) values.remove(pts)
        return value
    }
    fun clear() { values.clear(); size=0 }
}

/** Owns the active and pending resources associated with one rendition switch. */
internal class RenditionSwitchResources<Resource : Any>(
    initialActive: Resource? = null,
    private val close: (Resource) -> Unit,
) {
    private val lock = Any()

    @Volatile
    var active: Resource? = initialActive
        private set

    @Volatile
    var pending: Resource? = null
        private set

    @Volatile
    var retained: Resource? = null
        private set

    fun retain(resource: Resource) = synchronized(lock) {
        check(retained == null) { "a resource is already retained" }
        retained = resource
    }

    private fun releaseUnlessRetained(resource: Resource) {
        if (resource !== retained) close(resource)
    }

    fun replaceActive(resource: Resource) {
        val previous = synchronized(lock) {
            check(pending == null) { "cannot replace active resource during a pending switch" }
            active.also { active = resource }
        }
        previous?.let(::releaseUnlessRetained)
    }

    fun begin(resource: Resource) {
        synchronized(lock) {
            check(pending == null) { "rendition switch already has a pending resource" }
            pending = resource
        }
    }

    fun activate(expected: Resource): Boolean {
        val previous = synchronized(lock) {
            if (pending !== expected) return false
            active.also {
                active = expected
                pending = null
            }
        }
        previous?.let(::releaseUnlessRetained)
        return true
    }

    fun abort(expected: Resource): Boolean {
        val resource = synchronized(lock) {
            if (pending !== expected) return false
            pending.also { pending = null }
        }
        resource?.let(::releaseUnlessRetained)
        return true
    }

    fun close() {
        val resources = synchronized(lock) {
            listOfNotNull(pending, active, retained).distinct().also {
                pending = null
                active = null
                retained = null
            }
        }
        resources.forEach(close)
    }
}
