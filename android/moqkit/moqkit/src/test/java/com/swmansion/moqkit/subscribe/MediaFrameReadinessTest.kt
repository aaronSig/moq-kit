package com.swmansion.moqkit.subscribe

import dev.moq.MediaFrame as NativeMediaFrame
import org.junit.Assert.*
import org.junit.Test

class MediaFrameReadinessTest {
    @Test fun nativeReadinessIsStampedBeforePipelineDispatch() {
        val before = System.nanoTime()
        val frame = MediaFrame(NativeMediaFrame(payload = byteArrayOf(1, 2), timestampUs = 42uL, keyframe = true))
        val after = System.nanoTime()
        assertTrue(frame.nativeReadyNanos!! in before..after)
        assertEquals(42L, frame.timestampUs)
        assertArrayEquals(byteArrayOf(1, 2), frame.payload)
    }
    @Test fun applicationConstructedFramesDoNotInventNativeProvenance() {
        assertNull(MediaFrame(byteArrayOf(1), 42, true).nativeReadyNanos)
    }
}
