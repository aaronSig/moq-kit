package com.swmansion.moqkit.subscribe.internal.pipeline

import com.swmansion.moqkit.subscribe.PipelineContext
import com.swmansion.moqkit.subscribe.PipelineEvent
import com.swmansion.moqkit.subscribe.PipelineMediaKind
import com.swmansion.moqkit.subscribe.internal.playback.MediaFrameKind
import com.swmansion.moqkit.subscribe.internal.playback.PlaybackStatsTracker
import org.junit.Assert.*
import org.junit.Test

class PlaybackStallScopeTest {
    @Test fun onlyTheOutputRenditionCanStartOrEndAnAggregatePlaybackStall() {
        var now = 1_000_000L
        val tracker = PlaybackStatsTracker(clock = { now })
        tracker.beginSession(MediaFrameKind.VIDEO)
        fun ctx(name: String) = PipelineContext(name, PipelineMediaKind.VIDEO, now)
        fun render(name: String) = tracker.onPipelineEvent(PipelineEvent.FrameRendered(ctx(name), now / 1000, now))
        fun start(name: String) = tracker.onPipelineEvent(PipelineEvent.StallStarted(ctx(name), StallCause.PUBLISHER_IDLE))
        fun end(name: String) = tracker.onPipelineEvent(PipelineEvent.StallEnded(ctx(name), StallCause.PUBLISHER_IDLE, 1))
        fun stats() = tracker.snapshot(audioLatency = null, videoLatency = null).videoStalls
        render("720p")
        start("360p-standby")
        assertEquals("Standby pressure is not an output freeze", 0L, stats()?.count ?: 0)
        now += 2_000_000; start("720p")
        assertEquals(1L, stats()?.count)
        now += 3_000_000; end("360p-standby")
        now += 2_000_000
        assertEquals("An unrelated end must not truncate the real stall", 5L, stats()?.totalDuration?.toMillis())
        render("360p-standby")
        now += 5_000_000
        assertEquals("New output ends the old stall", 5L, stats()?.totalDuration?.toMillis())
        start("720p")
        assertEquals("The retired output cannot create a new stall", 1L, stats()?.count)
        start("360p-standby")
        assertEquals("A real stall on the new output must still count", 2L, stats()?.count)
        now += 2_000_000; render("360p-standby")
        assertEquals(7L, stats()?.totalDuration?.toMillis())
        tracker.reset()
        start("360p-standby")
        assertNull("A previous session cannot revive a reset counter", stats())
    }
}
