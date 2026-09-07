import Foundation
final class TestTime: PipelineTimeSource, @unchecked Sendable { var nowNanos:UInt64=1_000_000_000 }
var failures=0
func check(_ condition:Bool,_ message:String) {if !condition {failures+=1;print("FAIL:",message)}}
func frame(_ pts:Int64)->PipelineFrame<String> {PipelineFrame(payload:"",timestampUs:pts,keyframe:true,sizeBytes:0)}
let time=TestTime()
let audio=TrackTimeline(timeSource:time),video=TrackTimeline(timeSource:time)
_ = audio.onFrame(frame(10_000_000));_ = video.onFrame(frame(10_000_000))
let mapper=TimestampDomainMapper(audioTimeline:audio,videoTimeline:video)
check(mapper.videoOffsetUs(thresholdUs:2_000_000)==nil,"aligned start")
time.nowNanos+=60_000_000_000
_ = audio.onFrame(frame(70_000_000))
let staleRendition=TrackTimeline(timeSource:time)
_ = staleRendition.onFrame(frame(10_000_000))
mapper.setVideoTimeline(staleRendition)
check(mapper.videoOffsetUs(thresholdUs:2_000_000)==nil,"cold rendition backlog is not a new timestamp domain")
check(mapper.audioTimeUs(videoTimeUs:10_000_000,thresholdUs:2_000_000)==10_000_000,"old picture must not be retimestamped sixty seconds forward")
check(mapper.videoTimeUs(audioTimeUs:70_000_000,thresholdUs:2_000_000)==70_000_000,"playhead must not move back to cached video")
let fresh=TrackTimeline(timeSource:time);_ = fresh.onFrame(frame(70_000_000));mapper.setVideoTimeline(fresh)
check(mapper.audioTimeUs(videoTimeUs:70_000_000,thresholdUs:2_000_000)==70_000_000,"fresh rendition retains common timeline")
let a2=TrackTimeline(timeSource:time),v2=TrackTimeline(timeSource:time)
_ = a2.onFrame(frame(13_000_000));_ = v2.onFrame(frame(10_000_000))
let separate=TimestampDomainMapper(audioTimeline:a2,videoTimeline:v2)
check(separate.videoOffsetUs(thresholdUs:2_000_000)==3_000_000,"initial distinct domains remain supported")
check(separate.audioTimeUs(videoTimeUs:10_000_000,thresholdUs:2_000_000)==13_000_000,"forward fixed offset")
check(separate.videoTimeUs(audioTimeUs:13_000_000,thresholdUs:2_000_000)==10_000_000,"inverse fixed offset")
let waiting=TimestampDomainMapper(audioTimeline:nil,videoTimeline:v2)
check(waiting.videoOffsetUs(thresholdUs:2_000_000)==nil,"wait for both domains")
waiting.setAudioTimeline(a2)
check(waiting.videoOffsetUs(thresholdUs:2_000_000)==3_000_000,"calibrate when both domains arrive")
print("Timestamp mapping checks: 10; failures: \(failures)")
exit(failures==0 ? 0 : 1)
