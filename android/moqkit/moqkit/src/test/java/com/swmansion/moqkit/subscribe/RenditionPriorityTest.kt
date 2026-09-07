package com.swmansion.moqkit.subscribe
import dev.moq.Video
import dev.moq.Dimensions
import uniffi.moq.MoqContainer
import java.time.Duration
import org.junit.Assert.*
import org.junit.Test
class RenditionPriorityTest {
    @Test fun fourRungsKeepEveryLowerTrackAheadOfItsUpgrade() {
        val priorities = listOf(240u,360u,540u,720u).map { height ->
            val raw = Video(codec = "avc1", description = null, coded = Dimensions(1280u,height), displayAspect = null, bitrate = null, framerate = null, container = MoqContainer.Legacy)
            val info = VideoTrackInfo("video-$height", VideoTrackConfig(codec = "avc1", coded = VideoSize(1280u,height), displayRatio = null, bitrate = null, framerate = null), raw)
            MediaTrackRequest(info, Duration.ofMillis(700)).priority.toInt()
        }
        assertEquals(4, priorities.toSet().size)
        assertTrue(priorities.zipWithNext().all { (low,high) -> low > high })
        assertTrue(priorities.all { it < 80 })
    }
}
