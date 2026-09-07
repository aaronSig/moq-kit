import Foundation

enum RenditionSwitchState: Equatable {
    case steady
    case preparing(targetTrack: String, startedNanos: UInt64)
    case cuttingIn(targetTrack: String, keyframePtsUs: UInt64)
    case flushSwap(targetTrack: String)
}

enum RenditionSwitchDecision: Equatable {
    case wait
    case cutIn(keyframePtsUs: UInt64)
    case flushSwap
    case abort(targetTrack: String)
}

/// Pure authority for rendition switch phase, cut-in, flush, and timeout decisions.
final class RenditionSwitchController {
    private let policy: SwitchPolicy
    private var switchStartedNanos: UInt64?
    private var reservePressureStartedNanos: UInt64?

    private(set) var state: RenditionSwitchState = .steady

    init(policy: SwitchPolicy = PipelinePolicies.switch) {
        self.policy = policy
    }

    func begin(targetTrack: String, nowNanos: UInt64) {
        precondition(!targetTrack.isEmpty, "target track must not be empty")
        switchStartedNanos = nowNanos
        reservePressureStartedNanos = nil
        state = .preparing(targetTrack: targetTrack, startedNanos: nowNanos)
    }

    func shouldAbandonUpgrade(nowNanos: UInt64, bufferedAheadUs: UInt64, minimumAheadUs: UInt64) -> Bool {
        guard minimumAheadUs > 0, bufferedAheadUs < minimumAheadUs else {
            reservePressureStartedNanos = nil
            return false
        }
        // A healthy quarter-second arrival burst can briefly cross the normal
        // reserve watermark. Give that dip time to replenish, but never defer
        // cancellation once the remaining presentation reserve is critical.
        if bufferedAheadUs < min(minimumAheadUs, 200_000) { return true }
        let started = reservePressureStartedNanos ?? nowNanos
        reservePressureStartedNanos = started
        return nowNanos >= started && nowNanos-started >= 200_000_000
    }

    func canPromoteUpgrade(elapsedNanos: UInt64, bufferedAheadUs: UInt64, minimumAheadUs: UInt64) -> Bool {
        elapsedNanos >= 1_500_000_000 && bufferedAheadUs >= minimumAheadUs
    }

    func onKeyframeAvailable(
        activePtsUs: UInt64,
        keyframePtsUs: UInt64
    ) -> RenditionSwitchDecision {
        guard case .preparing(let target, _) = state else { return .wait }
        let gap = activePtsUs > keyframePtsUs ? activePtsUs - keyframePtsUs : 0
        // Aligned rendition tracks share their source clock. A cached keyframe
        // far behind that clock is backlog, not a new timestamp domain.
        guard gap <= UInt64(policy.cutInWindowUs) else { return .wait }
        state = .cuttingIn(targetTrack: target, keyframePtsUs: keyframePtsUs)
        return .wait
    }

    /// Losing an accepted keyframe invalidates readiness, not the original deadline.
    func retryPreparation() {
        guard case .cuttingIn(let target, _) = state, let started = switchStartedNanos else { return }
        state = .preparing(targetTrack: target, startedNanos: started)
    }

    func onActiveProgress(_ activePtsUs: UInt64) -> RenditionSwitchDecision {
        switch state {
        case .cuttingIn(_, let keyframePtsUs) where activePtsUs >= keyframePtsUs:
            return .cutIn(keyframePtsUs: keyframePtsUs)
        case .flushSwap:
            return .flushSwap
        default:
            return .wait
        }
    }

    func onTime(nowNanos: UInt64) -> RenditionSwitchDecision {
        let target: String
        switch state {
        case .preparing(let targetTrack, _), .cuttingIn(let targetTrack, _):
            target = targetTrack
        case .steady, .flushSwap:
            return .wait
        }
        guard let started = switchStartedNanos else { return .wait }
        let elapsed = nowNanos >= started ? nowNanos - started : 0
        guard elapsed >= UInt64(policy.keyframeTimeoutUs) * 1_000 else { return .wait }
        switchStartedNanos = nil
        reservePressureStartedNanos = nil
        state = .steady
        return .abort(targetTrack: target)
    }

    func shouldDiscardPendingDelta(activePtsUs: UInt64, framePtsUs: UInt64) -> Bool {
        activePtsUs > framePtsUs
            && activePtsUs - framePtsUs > UInt64(policy.cutInWindowUs)
    }

    func complete() {
        switchStartedNanos = nil
        reservePressureStartedNanos = nil
        state = .steady
    }
}
