package com.swmansion.moqkit.subscribe.internal.pipeline

import org.junit.Assert.*
import org.junit.Test

class UpgradeReserveGraceTest {
    @Test fun normalGroupedArrivalDipDoesNotCancelTheTrialImmediately() {
        val controller = RenditionSwitchController(SwitchPolicy())
        controller.begin("higher")
        assertFalse("A healthy group dip needs time to replenish",
            controller.shouldAbandonUpgrade(500_000, 525_000))
    }

    @Test fun sustainedPressureCancelsWithinTwoHundredMilliseconds() {
        val c = RenditionSwitchController(SwitchPolicy())
        c.begin("higher")
        assertFalse(c.shouldAbandonUpgrade(500_000, 525_000, 0))
        assertFalse(c.shouldAbandonUpgrade(450_000, 525_000, 199_000_000))
        assertTrue(c.shouldAbandonUpgrade(400_000, 525_000, 200_000_000))
    }

    @Test fun criticallyLowReserveNeverWaitsForGrace() {
        for (minimum in listOf(100_000L, 525_000L, 1_125_000L)) {
            val c = RenditionSwitchController(SwitchPolicy())
            c.begin("higher")
            assertTrue(c.shouldAbandonUpgrade(minOf(minimum, 200_000L) - 1, minimum, 0))
        }
    }

    @Test fun replenishmentResetsPressureButDoesNotResetTheSwitchDeadline() {
        val c = RenditionSwitchController(SwitchPolicy())
        c.begin("higher")
        assertFalse(c.shouldAbandonUpgrade(500_000, 525_000, 0))
        assertFalse(c.shouldAbandonUpgrade(700_000, 525_000, 150_000_000))
        assertFalse(c.shouldAbandonUpgrade(500_000, 525_000, 300_000_000))
        assertFalse(c.shouldAbandonUpgrade(450_000, 525_000, 450_000_000))
        assertEquals(SwitchDecision.Abort("higher"), c.onTimeout())
    }

    @Test fun aNewTrialDoesNotInheritThePreviousTrialsPressure() {
        val c = RenditionSwitchController(SwitchPolicy())
        c.begin("first")
        assertFalse(c.shouldAbandonUpgrade(500_000, 525_000, 0))
        c.complete()
        c.begin("second")
        assertFalse(c.shouldAbandonUpgrade(500_000, 525_000, 1_000_000_000))
        assertFalse(c.shouldAbandonUpgrade(0, 0, 2_000_000_000))
    }
}
