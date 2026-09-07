import Foundation
import Moq
@testable import MoQKit
import XCTest

final class LiveMediaSubscriptionTests: XCTestCase {
    func testExplicitLiveRequestWithoutAdapterFailsBeforeDemand() throws {
        let producer = try Moq.BroadcastProducer()
        let registry = MediaSubscriptionRegistry(broadcast: try producer.consume())
        XCTAssertThrowsError(try registry.subscribeMedia(MediaTrackRequest(
            name: "video", container: .legacy, startAtLiveEdge: true)))
        XCTAssertEqual(registry.activeSubscriptionCount, 0)
    }

    func testLiveAndCachedRequestsHaveIndependentRegistryOwners() async throws {
        let producer = try Moq.BroadcastProducer()
        let track = try producer.publishTrack(name: "video")
        let invoked = expectation(description: "Typed live adapter receives the request")
        let adapter: LiveMediaSubscriptionFactory = { consumer, name, container, preferences in
            XCTAssertEqual(name, "video")
            XCTAssertEqual(container, .legacy)
            XCTAssertEqual(preferences.priority, 60)
            XCTAssertEqual(preferences.latencyMaxMs, 700)
            // This fixture verifies SDK routing with the published API. The
            // private integration separately exercises subscribeMediaLive's ABI.
            let result = try await consumer.subscribeMedia(name: name, container: container, subscription: preferences)
            invoked.fulfill()
            return result
        }
        let registry = MediaSubscriptionRegistry(broadcast: try producer.consume(), liveMediaSubscriptionFactory: adapter)
        let cached = try registry.subscribeMedia(MediaTrackRequest(name: "video", container: .legacy))
        let live = try registry.subscribeMedia(MediaTrackRequest(name: "video", container: .legacy,
            targetBuffering: .milliseconds(700), priority: 60, startAtLiveEdge: true))
        await fulfillment(of: [invoked], timeout: 2)
        XCTAssertEqual(registry.activeSubscriptionCount, 2)
        cached.close()
        XCTAssertEqual(registry.activeSubscriptionCount, 1)
        live.close()
        XCTAssertEqual(registry.activeSubscriptionCount, 0)
        try track.finish()
        try producer.finish()
    }

    func testCatalogVideoOnlyOptsInWhenTheSourceHasTheCapability() throws {
        let producer = try Moq.BroadcastProducer()
        let track = VideoTrackInfo(name: "video", config: Moq.Video(
            codec: "avc1", description: nil, coded: .init(width: 640, height: 360),
            displayAspect: nil, bitrate: nil, framerate: nil, container: .legacy))
        let ordinary = BroadcastMediaSource(consumer: try producer.consume())
        XCTAssertFalse(ordinary.videoRequest(track: track, targetBuffering: .milliseconds(700)).startAtLiveEdge)
        let enabled = BroadcastMediaSource(consumer: try producer.consume(), liveMediaSubscriptionFactory: { consumer, name, container, preferences in
            try await consumer.subscribeMedia(name: name, container: container, subscription: preferences)
        })
        XCTAssertTrue(enabled.videoRequest(track: track, targetBuffering: .milliseconds(700)).startAtLiveEdge)
        XCTAssertFalse(MediaTrackRequest(name: "generic", container: .legacy).startAtLiveEdge)
    }
}
