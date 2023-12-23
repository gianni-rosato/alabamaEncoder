import os

from alabamaEncode.conent_analysis.chunk.final_encode_steps.final_encode_step import (
    FinalEncodeStep,
)
from alabamaEncode.core.alabama import AlabamaContext
from alabamaEncode.encoder.encoder import Encoder
from alabamaEncode.encoder.impl.Svtenc import EncoderSvt
from alabamaEncode.encoder.impl.X264 import EncoderX264
from alabamaEncode.encoder.rate_dist import EncoderRateDistribution
from alabamaEncode.encoder.stats import EncodeStats
from alabamaEncode.metrics.comp_dis import ComparisonDisplayResolution
from alabamaEncode.metrics.vmaf.options import VmafOptions
from alabamaEncode.scene.chunk import ChunkObject


class TargetX264(FinalEncodeStep):
    def run(
        self, enc: Encoder, chunk: ChunkObject, ctx: AlabamaContext, encoded_a_frame
    ) -> EncodeStats:
        if not isinstance(enc, EncoderX264):
            raise Exception("TargetX264 only supports x264")

        target_vmaf = ctx.vmaf

        from alabamaEncode.adaptive.helpers.probe_file_path import get_probe_file_base

        probe_file_base = get_probe_file_base(chunk.chunk_path)

        enc.x264_tune = ""
        enc.x264_ipratio = 1.3
        enc.x264_mbtree = True
        enc.x264_aq_strength = 0.4
        enc.x264_merange = 36
        enc.x264_bframes = 8
        enc.x264_rc_lookahead = 48
        enc.x264_ref = 4
        enc.x264_me = "umh"
        enc.x264_subme = 10
        enc.x264_non_deterministic = True
        enc.x264_vbv_maxrate = 25000
        enc.x264_vbv_bufsize = 31250
        enc.speed = 0

        def get_score(_crf):
            enc.crf = _crf
            enc.output_path = os.path.join(
                probe_file_base,
                f"convexhull.{_crf}{enc.get_chunk_file_extension()}",
            )
            enc.speed = 0  # max(ctx.prototype_encoder.speed + 2, 5)
            enc.passes = 1
            enc.threads = 1
            enc.override_flags = None
            enc.rate_distribution = EncoderRateDistribution.CQ

            stats: EncodeStats = enc.run(
                calculate_vmaf=True,
                vmaf_params=VmafOptions(
                    uhd=ctx.vmaf_4k_model,
                    phone=ctx.vmaf_phone_model,
                    ref=ComparisonDisplayResolution.from_string(
                        ctx.vmaf_reference_display
                    )
                    if ctx.vmaf_reference_display
                    else None,
                    no_motion=ctx.vmaf_no_motion,
                ),
                override_if_exists=False,
            )
            match ctx.vmaf_target_representation:
                case "mean":
                    statistical_representation = stats.vmaf_result.mean
                case "harmonic_mean":
                    statistical_representation = stats.vmaf_result.harmonic_mean
                case "max":
                    statistical_representation = stats.vmaf_result.max
                case "min":
                    statistical_representation = stats.vmaf_result.min
                case "median":
                    statistical_representation = stats.vmaf_result.percentile_50
                case "percentile_1":
                    statistical_representation = stats.vmaf_result.percentile_1
                case "percentile_5":
                    statistical_representation = stats.vmaf_result.percentile_5
                case "percentile_10":
                    statistical_representation = stats.vmaf_result.percentile_10
                case "percentile_25":
                    statistical_representation = stats.vmaf_result.percentile_25
                case "percentile_50":
                    statistical_representation = stats.vmaf_result.percentile_50
                case _:
                    raise Exception(
                        f"Unknown vmaf_target_representation {ctx.vmaf_target_representation}"
                    )
            return statistical_representation

        probes = ctx.vmaf_probe_count
        trys = []
        low_crf = 20 if isinstance(enc, EncoderSvt) else 10
        high_crf = 55 if target_vmaf > 90 else 63
        epsilon = 0.1
        depth = 0
        mid_crf = 0
        while low_crf <= high_crf and depth < probes:
            mid_crf = (low_crf + high_crf) // 2

            # don't try the same crf twice
            if mid_crf in [t[0] for t in trys]:
                break

            statistical_representation = get_score(mid_crf)

            ctx.log(
                f"{chunk.log_prefix()} crf: {mid_crf} vmaf: {statistical_representation} "
                f"attempt {depth + 1}/{probes}",
                category="probe",
            )

            if abs(statistical_representation - target_vmaf) <= epsilon:
                break
            elif statistical_representation > target_vmaf:
                low_crf = mid_crf + 1
            else:
                high_crf = mid_crf - 1
            trys.append((mid_crf, statistical_representation))
            depth += 1

        # if we didn't get it right on the first,
        # via linear interpolation, try to find the crf that is closest to target vmaf
        if len(trys) > 1:
            # sort by vmaf difference from target
            points = sorted(trys, key=lambda _x: abs(_x[1] - target_vmaf))

            # get the two closest points
            crf_low, vmaf_low = points[0]
            crf_high, vmaf_high = points[1]

            # means multiple probes got the same score, aka all back screens etc
            if not vmaf_high - vmaf_low == 0:
                interpolated_crf = crf_low + (crf_high - crf_low) * (
                    (target_vmaf - vmaf_low) / (vmaf_high - vmaf_low)
                )

                # if 28-37 are crf points closes to target,
                # we clamp the interpolated crf to 18-41
                clamp_low = min(crf_low, crf_high) - 10
                clamp_high = max(crf_low, crf_high) + 4
                interpolated_crf = max(min(interpolated_crf, clamp_high), clamp_low)

                if enc.supports_float_crfs():
                    crf = interpolated_crf
                else:
                    crf = int(interpolated_crf)
            else:
                crf = mid_crf
        else:
            crf = mid_crf

        ctx.log(f"{chunk.log_prefix()}Decided on crf: {crf}", category="probe")

        # measure bitrate of crf at config settings
        enc.crf = crf
        enc.passes = 1
        enc.x264_collect_pass = True
        enc.rate_distribution = EncoderRateDistribution.CQ
        enc.output_path = chunk.chunk_path
        stats = enc.run()

        ctx.log(
            f"{chunk.log_prefix()}bitrate test at crf {crf} got {stats.bitrate} k/s,"
            f" encoding three pass vbr at {stats.bitrate} k/s ",
            category="probe",
        )

        enc.passes = -3
        enc.rate_distribution = EncoderRateDistribution.VBR
        enc.bitrate = stats.bitrate

        return enc.run(
            calculate_vmaf=True,
            vmaf_params=VmafOptions(
                uhd=ctx.vmaf_4k_model,
                phone=ctx.vmaf_phone_model,
                ref=ComparisonDisplayResolution.from_string(ctx.vmaf_reference_display)
                if ctx.vmaf_reference_display
                else None,
                no_motion=ctx.vmaf_no_motion,
            ),
            on_frame_encoded=encoded_a_frame,
        )

    def dry_run(self, enc: Encoder, chunk: ChunkObject) -> str:
        raise Exception("dry_run not implemented for TargetX264")