import CoreMedia
@testable import MoQKit
import XCTest

final class AudioDrivenClockTests: XCTestCase {
    func testZeroIsOnlyAnAnchoredMediaTimeAfterExplicitInitialization() throws {
        let clock = try makeAudioDrivenClock()
        XCTAssertFalse(clock.isAnchored)
        clock.setRate(0)
        XCTAssertFalse(clock.isAnchored)
        clock.setTimeUs(0)
        XCTAssertTrue(clock.isAnchored)
        XCTAssertEqual(clock.currentTimeUs, 0)
    }

    func testVideoClockAlsoRequiresAnExplicitTimestampAnchor() {
        let clock = VideoDrivenClock()
        XCTAssertFalse(clock.isAnchored)
        clock.setRate(0)
        XCTAssertFalse(clock.isAnchored)
        clock.setRate(0, timeUs: 0)
        XCTAssertTrue(clock.isAnchored)
    }

    @MainActor
    func testStartupLatencyIsUnknownUntilTheClockHasAnAnchor() {
        XCTAssertNil(PlaybackPipeline.playbackLatency(liveTime: 10_638_410_621, currentTimeUs: nil))
        XCTAssertNil(PlaybackPipeline.latencyUs(liveTime: 10_638_410_621, currentTimeUs: nil))
        XCTAssertEqual(PlaybackPipeline.playbackLatency(liveTime: 1_000, currentTimeUs: 0), .microseconds(1_000))
        XCTAssertEqual(PlaybackPipeline.latencyUs(liveTime: 1_000, currentTimeUs: 0), 1_000)
    }
    func testSetTimeUpdatesCurrentTime() throws {
        let timebase = try makeAudioDrivenClock()

        timebase.setTimeUs(123_456)

        XCTAssertEqual(timebase.currentTime().value, 123_456, accuracy: 1)
        XCTAssertEqual(timebase.currentTime().timescale, 1_000_000)
        XCTAssertEqual(timebase.currentTimeUs, 123_456, accuracy: 1)
    }
}

private func makeAudioDrivenClock() throws -> AudioDrivenClock {
    try AudioDrivenClock()
}
