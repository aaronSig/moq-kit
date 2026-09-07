package com.swmansion.moqkit.subscribe.internal.pipeline

import org.junit.Assert.*
import org.junit.Test
import com.swmansion.moqkit.subscribe.MediaFrame

class DrainingSwitchTest {
    @Test fun equalPtsFromNewRenditionCannotOverwriteQueuedOldFrameIdentity() {
        val q=DecoderMetadataQueue<String>()
        q[100]="old";q[100]="new";q[200]="following"
        assertEquals(3,q.size)
        assertEquals("old",q.remove(100));assertEquals("new",q.remove(100))
        assertNull(q.remove(100));assertEquals(1,q.size)
        q.clear();assertEquals(0,q.size);assertNull(q.remove(200))
    }
    @Test fun retainedFallbackSurvivesUpgradesAndAbandonedSwitchesButClosesOnStop() {
        val closed=mutableListOf<String>()
        val r=RenditionSwitchResources<String>(close={closed+=it})
        val low="low";val high="high"
        r.replaceActive(low);r.retain(low);r.begin(high);assertTrue(r.activate(high))
        assertTrue(closed.isEmpty())
        r.begin(low);assertTrue(r.abort(low));assertTrue(closed.isEmpty())
        r.begin(low);assertTrue(r.activate(low));assertEquals(listOf(high),closed)
        r.close();assertEquals(listOf(high,low),closed)
        r.close();assertEquals(2,closed.size)
    }
    @Test fun upgradeKeepsTheNewestOverlappingKeyframeDecodable() {
        val b=FrameBuffer(AdmissionPolicy())
        for(i in 0..11)b.offer(TimedFrame(MediaFrame(byteArrayOf(1),i*100_000L,i%5==0),epoch=1))
        assertEquals(5,b.discardBeforeNewestKeyframe(900_000))
        assertEquals(500_000L,b.peekFront()?.timestampUs)
        assertEquals(500_000L,b.pollPlayable(0)?.timestampUs)
        assertEquals(0,b.discardBeforeNewestKeyframe(900_000))
        assertEquals(4,b.discardBeforeNewestKeyframe(1_050_000))
        assertEquals(1_000_000L,b.pollPlayable(0)?.timestampUs)
    }
    @Test fun abandonAnUpgradeWhileTheOldPictureStillHasTimeToDrain() {
        val c=RenditionSwitchController(SwitchPolicy())
        assertFalse(c.shouldAbandonUpgrade(700_000,750_000,0))
        assertTrue(c.shouldAbandonUpgrade(700_000,750_000,200_000_000))
        assertFalse(c.shouldAbandonUpgrade(900_000,750_000))
        assertFalse(c.shouldAbandonUpgrade(0,0)) // A downshift must continue.
    }
    @Test fun promotionRequiresHealthyPendingLeadWithoutInflatingTheActiveReserve() {
        val c=RenditionSwitchController(SwitchPolicy())
        assertFalse(c.canPromoteUpgrade(2_000_000_000L,600_000,850_000))
        assertTrue(c.canPromoteUpgrade(2_000_000_000L,900_000,850_000))
        assertFalse(c.shouldAbandonUpgrade(800_000,750_000))
    }
    @Test fun overlapSuppressionMustKeepAlreadyDecodedOldFrames() {
        val c=RenditionSwitchController(SwitchPolicy())
        assertFalse(c.shouldSuppressOverlap(false,900_000,1_000_000))
        assertTrue(c.shouldSuppressOverlap(true,900_000,1_000_000))
        assertFalse(c.shouldSuppressOverlap(true,1_041_666,1_000_000))
    }
    @Test fun cachedKeyframeBurstDoesNotProveSustainableUpgrade() {
        val c=RenditionSwitchController(SwitchPolicy())
        assertFalse(c.canPromoteUpgrade(100_000_000L,1_000_000,500_000))
        assertFalse(c.canPromoteUpgrade(2_000_000_000L,200_000,500_000))
        assertTrue(c.canPromoteUpgrade(2_000_000_000L,900_000,500_000))
    }
    @Test fun cancelledOldTrackCanCutToFutureKeyframeWithoutFurtherOldFrames() {
        val c=RenditionSwitchController(SwitchPolicy())
        c.begin("low")
        assertEquals(SwitchDecision.Wait,c.onKeyframeAvailable(1_000_000,1_200_000))
        assertEquals(SwitchDecision.Wait,c.onActiveProgress(1_000_000))
        assertEquals(SwitchDecision.CutIn(1_200_000),c.onActiveExhausted())
        c.complete()
        assertEquals(SwitchDecision.Wait,c.onActiveExhausted())
    }
    @Test fun keyframeArrivalDoesNotDisableTimeoutForAnIncompleteCutIn() {
        val c=RenditionSwitchController(SwitchPolicy())
        c.begin("pending")
        c.onKeyframeAvailable(1_000_000,2_000_000)
        assertEquals(SwitchDecision.Abort("pending"),c.onTimeout())
        assertEquals(SwitchState.Steady,c.state)
    }
    @Test fun exhaustedActiveStillRequiresADecodableKeyframe() {
        val c=RenditionSwitchController(SwitchPolicy())
        c.begin("pending")
        assertEquals(SwitchDecision.Wait,c.onActiveExhausted())
    }
}
