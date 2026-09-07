import Foundation
import Moq
@testable import MoQKit
import XCTest

final class PausedTrackSelectionTests: XCTestCase {
    @MainActor
    func testChangingRenditionsWhilePausedDoesNotRestartPlayback() async throws {
        let producer = try Moq.BroadcastProducer()
        let video = Moq.Video(codec: "avc1", description: nil,
                              coded: .init(width: 640, height: 360), displayAspect: nil,
                              bitrate: 300_000, framerate: 30, container: .legacy)
        let audio = Moq.Audio(codec: "opus", description: nil, sampleRate: 48_000,
                              channelCount: 2, bitrate: nil, container: .legacy)
        let catalog = Catalog(path: "test/paused", catalog: Moq.Catalog(
            video: ["video-high": video, "video-low": video],
            audio: ["audio-main": audio, "audio-other": audio],
            display: nil, rotation: nil, flip: nil, sections: [:]
        ), mediaSource: BroadcastMediaSource(consumer: try producer.consume()))
        let player = try Player(catalog: catalog, videoTrackName: "video-high", audioTrackName: "audio-main")
        await player.pause()

        let selected = expectation(description: "Paused selection changes are reported")
        selected.expectedFulfillmentCount = 2
        let restarted = expectation(description: "Paused selection must not start a pipeline")
        restarted.isInverted = true
        let observer = player.subscribeEvents { event in
            if case .trackSelect = event.type { selected.fulfill() }
            if case .playbackRequest = event.type { restarted.fulfill() }
            if case .playbackResume = event.type { restarted.fulfill() }
            if case .trackSubscribeStart = event.type { restarted.fulfill() }
        }
        try await player.switchTrack(to: "video-low")
        try await player.switchAudioTrack(to: "audio-other")
        await fulfillment(of: [selected, restarted], timeout: 0.2)
        XCTAssertNil(player.videoBufferedAhead)
        XCTAssertFalse(player.hasPendingVideoSwitch)
        observer.cancel()
        await player.stopAll()
    }
}
