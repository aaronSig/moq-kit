#!/usr/bin/env python3
"""Compile the actual pinned mapper/timeline sources with deterministic clock regressions."""
from pathlib import Path
import subprocess,tempfile
lab=Path(__file__).resolve().parents[2]
source=lab/'ios/Sources/MoQKit'
pipeline=source/'Subscribe/internal/pipeline'
with tempfile.TemporaryDirectory(prefix='moq-ios-timestamps-') as folder:
    folder=Path(folder)
    # Only omit unrelated PipelineContext adapters; the clock/frame definitions
    # are verbatim upstream, and mapper/timeline/policies/lock compile in full.
    models=(pipeline/'PipelineModels.swift').read_text().split('\nextension PipelineMediaKind',1)[0]
    (folder/'PipelineModels.swift').write_text(models)
    binary=folder/'timestamps'
    subprocess.run(['swiftc','-module-cache-path',str(folder/'modules'),str(source/'Internal/Shared/UnfairLock.swift'),str(pipeline/'PipelinePolicies.swift'),str(pipeline/'TrackTimeline.swift'),str(pipeline/'TimestampDomainMapper.swift'),str(folder/'PipelineModels.swift'),str(lab/'tests/regressions/timestamps/main.swift'),'-o',str(binary)],check=True)
    raise SystemExit(subprocess.run([str(binary)]).returncode)
